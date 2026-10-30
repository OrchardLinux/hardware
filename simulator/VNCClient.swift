import AppKit
import Darwin

// Local QEMU RFB client. Raw rectangles keep this first draft small and avoid
// an extra guest-side streaming server. It connects exclusively to loopback.
final class VNCClient {
    private let lock = NSLock()
    private var socketFD: Int32 = -1
    private var running = true
    var onFrame: ((CGImage) -> Void)?
    var onConnection: ((Bool) -> Void)?
    private var width = 0
    private var height = 0
    private var pixels = Data()

    func start() { DispatchQueue.global(qos: .userInitiated).async { [weak self] in self?.connectLoop() } }
    func stop() {
        lock.lock(); running = false; let fd = socketFD; socketFD = -1; lock.unlock()
        if fd >= 0 { shutdown(fd, SHUT_RDWR); close(fd) }
    }
    private func connectLoop() {
        while true {
            lock.lock(); let keepRunning = running; lock.unlock()
            if !keepRunning { return }
            let fd = socket(AF_INET, SOCK_STREAM, 0)
            if fd < 0 { Thread.sleep(forTimeInterval: 1); continue }
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = in_port_t(5908).bigEndian
            address.sin_addr = in_addr(s_addr: in_addr_t(0x7f000001).bigEndian)
            let result = withUnsafePointer(to: &address) { p in
                p.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
            if result != 0 { close(fd); Thread.sleep(forTimeInterval: 1); continue }
            lock.lock()
            if !running { lock.unlock(); close(fd); return }
            socketFD = fd; lock.unlock()
            do {
                try handshake(fd)
                DispatchQueue.main.async { [weak self] in self?.onConnection?(true) }
                try receiveUpdates(fd)
            } catch { NSLog("VM display disconnected: %@", String(describing: error)) }
            lock.lock(); let ownsFD = socketFD == fd; if ownsFD { socketFD = -1 }; lock.unlock()
            if ownsFD { close(fd) }
            DispatchQueue.main.async { [weak self] in self?.onConnection?(false) }
            Thread.sleep(forTimeInterval: 1)
        }
    }
    enum Failure: Error { case closed, protocolError }
    private func read(_ fd: Int32, _ count: Int) throws -> Data {
        var data = Data(count: count)
        let ok = data.withUnsafeMutableBytes { (buffer: UnsafeMutableRawBufferPointer) -> Bool in
            var offset = 0
            while offset < count {
                let n = recv(fd, buffer.baseAddress!.advanced(by: offset), count - offset, 0)
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
        if !ok { throw Failure.closed }
        return data
    }
    private func sendBytes(_ bytes: [UInt8]) {
        lock.lock(); defer { lock.unlock() }
        guard socketFD >= 0 else { return }
        bytes.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let n = Darwin.send(socketFD, buffer.baseAddress!.advanced(by: offset), buffer.count - offset, MSG_NOSIGNAL)
                if n <= 0 { break }
                offset += n
            }
        }
    }
    private func u16(_ n: Int) -> [UInt8] { [UInt8((n >> 8) & 255), UInt8(n & 255)] }
    private func u32(_ n: UInt32) -> [UInt8] { [UInt8((n >> 24) & 255), UInt8((n >> 16) & 255), UInt8((n >> 8) & 255), UInt8(n & 255)] }
    private func number16(_ d: Data, _ offset: Int) -> Int { Int(d[offset]) << 8 | Int(d[offset + 1]) }
    private func number32(_ d: Data, _ offset: Int) -> UInt32 { UInt32(d[offset]) << 24 | UInt32(d[offset+1]) << 16 | UInt32(d[offset+2]) << 8 | UInt32(d[offset+3]) }
    private func handshake(_ fd: Int32) throws {
        let version = try read(fd, 12)
        guard String(data: version, encoding: .ascii)?.hasPrefix("RFB 003.") == true else { throw Failure.protocolError }
        sendBytes(Array("RFB 003.008\n".utf8))
        let count = Int(try read(fd, 1)[0]); guard count > 0 && count < 32 else { throw Failure.protocolError }
        guard try read(fd, count).contains(1) else { throw Failure.protocolError }
        sendBytes([1]); guard try number32(read(fd, 4), 0) == 0 else { throw Failure.protocolError }
        sendBytes([1])
        let initial = try read(fd, 24)
        let initialWidth = number16(initial, 0), initialHeight = number16(initial, 2)
        guard initialWidth > 0 && initialHeight > 0 && initialWidth <= 4096 && initialHeight <= 4096 else { throw Failure.protocolError }
        lock.lock(); width = initialWidth; height = initialHeight; lock.unlock()
        let nameLength = Int(number32(initial, 20)); guard nameLength < 65536 else { throw Failure.protocolError }
        _ = try read(fd, nameLength)
        pixels = Data(count: width * height * 4)
        // 32-bit little-endian true color: bytes B,G,R,unused.
        sendBytes([0,0,0,0, 32,24,0,1, 0,255,0,255,0,255, 16,8,0,0,0,0])
        // Raw rectangles and DesktopSize pseudo-encoding.
        sendBytes([2,0,0,2] + u32(0) + u32(UInt32(bitPattern: -223)))
        request(incremental: false)
    }
    private func request(incremental: Bool) { sendBytes([3, incremental ? 1 : 0, 0,0,0,0] + u16(width) + u16(height)) }
    private func receiveUpdates(_ fd: Int32) throws {
        while true {
            let type = try read(fd, 1)[0]
            if type == 2 { continue } // bell
            if type == 3 { // server clipboard: ignore but consume it
                let header = try read(fd, 7); let len = Int(number32(header, 3)); guard len < 4_000_000 else { throw Failure.protocolError }
                _ = try read(fd, len); continue
            }
            guard type == 0 else { throw Failure.protocolError }
            let header = try read(fd, 3); let count = number16(header, 1)
            guard count < 8192 else { throw Failure.protocolError }
            var changed = false
            var resized = false
            for _ in 0..<count {
                let rect = try read(fd, 12)
                let x = number16(rect, 0), y = number16(rect, 2), w = number16(rect, 4), h = number16(rect, 6)
                let encoding = Int32(bitPattern: number32(rect, 8))
                if encoding == -223 {
                    guard w > 0 && h > 0 && w <= 4096 && h <= 4096 else { throw Failure.protocolError }
                    lock.lock(); width = w; height = h; lock.unlock()
                    pixels = Data(count: w * h * 4); resized = true; continue
                }
                guard encoding == 0 && w > 0 && h > 0 && x + w <= width && y + h <= height else { throw Failure.protocolError }
                let data = try read(fd, w * h * 4)
                pixels.withUnsafeMutableBytes { (dst: UnsafeMutableRawBufferPointer) in
                    data.withUnsafeBytes { (src: UnsafeRawBufferPointer) in
                        for row in 0..<h {
                            memcpy(dst.baseAddress!.advanced(by: ((y + row) * width + x) * 4), src.baseAddress!.advanced(by: row * w * 4), w * 4)
                        }
                    }
                }
                changed = true
            }
            if changed {
                let snapshot = pixels as CFData
                if let provider = CGDataProvider(data: snapshot), let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue), provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) {
                    DispatchQueue.main.async { [weak self] in self?.onFrame?(image) }
                }
            }
            request(incremental: !resized)
        }
    }
    func pointer(x: CGFloat, y: CGFloat, buttons: UInt8) {
        // The viewer sends normalized coordinates; framebuffer is normally 768x1024.
        lock.lock(); let w = width, h = height; lock.unlock()
        guard w > 0 && h > 0 else { return }
        let px = max(0, min(w-1, Int(x * CGFloat(w))))
        let py = max(0, min(h-1, Int(y * CGFloat(h))))
        sendBytes([5, buttons] + u16(px) + u16(py))
    }
    func key(_ keysym: UInt32, down: Bool) { sendBytes([4, down ? 1 : 0, 0, 0] + u32(keysym)) }
}
