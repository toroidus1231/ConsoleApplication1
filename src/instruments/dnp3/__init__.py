"""DNP3 (Distributed Network Protocol 3) master implementation per
IEEE 1815-2012, used for protective-relay commissioning.

Layered to mirror the DNP3 stack:
  datalink.py    - 10-byte header, CRC-16-ANSI per block
  transport.py   - segmentation across multiple link frames
  application.py - function codes, IIN bits, request/response framing
  objects.py     - object groups 1, 2, 30, 32, 50, 70 encode/decode
  master.py      - high-level master operations
"""
