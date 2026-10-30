import unittest

from orchard_cli.transports.ssh import ssh_argv


class SSHLifecycle(unittest.TestCase):
    def test_final_rpc_cannot_leave_a_multiplexed_connection_alive(self):
        argv = ssh_argv('identity', 'known_hosts', '172.16.42.1', 22)
        for option in ('ControlMaster=no', 'ControlPath=none', 'ControlPersist=no'):
            self.assertIn(option, argv)



if __name__ == '__main__':unittest.main()
