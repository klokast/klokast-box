"""Decode real DNS-SD wire shapes, including compressed records and bad input."""
import runpy
import struct
import unittest
from pathlib import Path

probe = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'apps/print-server/bin/print-lan-check'))


def wire_name(value):
    return b''.join(bytes([len(label)]) + label.encode() for label in value.split('.')) + b'\0'


class BonjourProbeTest(unittest.TestCase):
    def test_compressed_service_answer(self):
        owner = '_ipp._tcp.local'
        instance = 'Epson XP-55 (boxa).' + owner
        host = 'boxa-bak-printer.local'
        question = wire_name(owner) + struct.pack('!HH', 12, 1)
        # PTR owner references the question; SRV/TXT use full instance names.
        rows = [(b'\xc0\x0c', 12, wire_name(instance)),
                (wire_name(instance), 33, struct.pack('!HHH', 0, 0, 631) + wire_name(host)),
                (wire_name(instance), 16, b'\x11rp=printers/epson'),
                (wire_name(host), 1, b'\xc0\xa8\x01\x9b')]
        packet = struct.pack('!6H', 1, 0x8400, 1, 1, 0, 3) + question
        for name, kind, value in rows:
            packet += name + struct.pack('!HHIH', kind, 1, 120, len(value)) + value
        records = probe['records'](packet)
        self.assertIn((owner, 12, instance), records)
        self.assertIn((instance, 33, (host, 631)), records)
        self.assertIn((instance, 16, ['rp=printers/epson']), records)
        self.assertIn((host, 1, '192.168.1.155'), records)

    def test_compression_cycle_refused(self):
        with self.assertRaises(ValueError):
            probe['name'](b'\xc0\x00', 0)

    def test_queries_are_not_answers(self):
        packet = struct.pack('!6H', 1, 0, 0, 0, 0, 0)
        self.assertEqual(probe['records'](packet), [])
