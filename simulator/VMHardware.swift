import AppKit
import Darwin

// Host side of the VM-only hardware: QMP injects real key events, while the
// virtio-serial display channel reports the guest's actual backlight value.
final class VMHardware {
    private let directory: String
    // QEMU's socket chardev accepts one QMP client at a time. Touch and
    // hardware keys must share this ordered connection, not compete for it.
    private let touchQueue = DispatchQueue(label: "org.orchard.simulator.input")
    private var touchFD: Int32 = -1 // accessed only on touchQueue
    var onBrightness: ((CGFloat) -> Void)?

    init(directory: String) { self.directory = directory }
    private func connect(_ name: String) -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return -1 }
        var address = sockaddr_un()
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        address.sun_family = sa_family_t(AF_UNIX)
        let path = Array((directory + "/" + name).utf8) + [0]
        let fits = withUnsafeMutableBytes(of: &address.sun_path) { buffer -> Bool in
            guard path.count <= buffer.count else { return false }
            buffer.copyBytes(from: path)
            return true
        }
        guard fits else { close(fd); return -1 }
        let result = withUnsafePointer(to: &address) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        if result != 0 { close(fd); return -1 }
        return fd
    }
    func start() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            while true {
                let fd = self.connect("display.sock")
                if fd < 0 { Thread.sleep(forTimeInterval: 1); continue }
                var pending = ""
                var buffer = [UInt8](repeating: 0, count: 512)
                while true {
                    let count = recv(fd, &buffer, buffer.count, 0)
                    if count <= 0 { break }
                    pending += String(decoding: buffer.prefix(count), as: UTF8.self)
                    if pending.count > 4096 { pending = "" }
                    while let newline = pending.firstIndex(of: "\n") {
                        let line = pending[..<newline]
                        pending.removeSubrange(...newline)
                        let values = line.split(separator: " ").compactMap { Double($0) }
                        if values.count == 2 && values[1] > 0 {
                            let brightness = CGFloat(max(0, min(1, values[0] / values[1])))
                            DispatchQueue.main.async { [weak self] in self?.onBrightness?(brightness) }
                        }
                    }
                }
                close(fd)
                Thread.sleep(forTimeInterval: 1)
            }
        }
    }
    func resetTouchConnection() {
        touchQueue.async { [weak self] in
            guard let self, self.touchFD >= 0 else { return }
            close(self.touchFD)
            self.touchFD = -1
        }
    }

    private func receiveInput() -> [String: Any]? {
        while true {
            var line = Data()
            var c: UInt8 = 0
            while line.count < 65536 {
                guard recv(touchFD, &c, 1, 0) == 1 else { return nil }
                if c == 10 { break }
                line.append(c)
            }
            guard let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else { return nil }
            if object["event"] == nil { return object }
        }
    }

    private func sendInput(_ object: [String: Any]) -> Bool {
        guard var data = try? JSONSerialization.data(withJSONObject: object) else { return false }
        data.append(10)
        return data.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let n = Darwin.send(touchFD, buffer.baseAddress!.advanced(by: offset), buffer.count - offset, MSG_NOSIGNAL)
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
    }

    private func inputCommand(_ object: [String: Any]) -> Bool {
        guard sendInput(object), let reply = receiveInput() else { return false }
        if let error = reply["error"] { NSLog("Simulator QMP input failed: %@", String(describing: error)) }
        return reply["return"] != nil
    }

    private func ensureInputConnection() -> Bool {
        if touchFD >= 0 { return true }
        touchFD = connect("qmp.sock")
        guard touchFD >= 0 else { NSLog("Simulator hardware control unavailable"); return false }
        var timeout = timeval(tv_sec: 2, tv_usec: 0)
        _ = setsockopt(touchFD, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        guard receiveInput()?["QMP"] != nil, inputCommand(["execute": "qmp_capabilities"]) else {
            close(touchFD); touchFD = -1; return false
        }
        // Clear a possible stale finger after a disconnected client.
        let end: [String: Any] = ["type": "mtt", "data": ["type": "end", "slot": 0, "tracking-id": -1, "axis": "x", "value": 0]]
        _ = inputCommand(["execute": "input-send-event", "arguments": ["events": [end]]])
        return true
    }

    // QMP coordinates use the 0..32767 absolute input range.
    func touch(_ phase: String, x: CGFloat, y: CGFloat) {
        let px = Int(max(0, min(1, x)) * 32767)
        let py = Int(max(0, min(1, y)) * 32767)
        touchQueue.async { [weak self] in
            guard let self else { return }
            guard self.ensureInputConnection() else { return }
            let tracking = phase == "end" ? -1 : 0
            func mt(_ kind: String, _ axis: String, _ value: Int) -> [String: Any] {
                ["type": "mtt", "data": ["type": kind, "slot": 0, "tracking-id": tracking, "axis": axis, "value": value]]
            }
            var events: [[String: Any]] = [mt(phase, "x", 0)]
            if phase != "end" {
                events.append(["type": "btn", "data": ["button": "touch", "down": true]])
                events.append(mt("data", "x", px))
                events.append(mt("data", "y", py))
            } else {
                events.append(["type": "btn", "data": ["button": "touch", "down": false]])
            }
            if !self.inputCommand(["execute": "input-send-event", "arguments": ["events": events]]) {
                NSLog("Simulator touch input failed")
                close(self.touchFD); self.touchFD = -1
            }
        }
    }
    func key(_ name: String, down: Bool) {
        touchQueue.async { [weak self] in
            guard let self, self.ensureInputConnection() else { return }
            let event: [String: Any] = ["type": "key", "data": ["down": down, "key": ["type": "qcode", "data": name]]]
            if !self.inputCommand(["execute": "input-send-event", "arguments": ["events": [event]]]) {
                close(self.touchFD); self.touchFD = -1
            }
        }
    }
}
