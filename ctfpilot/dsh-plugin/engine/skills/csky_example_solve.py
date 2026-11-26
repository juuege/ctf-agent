import os, struct, sys

def rc4_ks(key, n):
    S = list(range(256)); j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 0xFF
        S[i], S[j] = S[j], S[i]
    i = j = 0; ks = []
    for k in range(n):
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        ks.append(S[(S[i] + S[j]) & 0xFF])
    return ks

_p = os.environ.get('CSKY_BIN', '') or (sys.argv[1] if len(sys.argv) > 1 else '')
if not _p:
    raise SystemExit('usage: set CSKY_BIN=<binary path> or pass the binary as argv[1]')
b = open(_p, 'rb').read()
def va2off(va):
    return va - 0x8470 + 0x470
ct = b[va2off(0x8aa0):va2off(0x8aa0) + 42]
print('ct  :', ct.hex())
ks = rc4_ks(b'testkey', 42)
W = bytes(c ^ k for c, k in zip(ct, ks))
print('W   :', W.hex())
print('ascii check W:', bytes(w if 0x20 <= w < 0x7f else ord('.') for w in W))

inp = bytearray(42)
inp[0] = ord('f')
for i in range(41):
    inp[i + 1] = inp[i] ^ W[i]
print('input:', inp.decode('latin1'))
print('repr :', repr(bytes(inp)))

# verify: recompute full pipeline
work = bytearray(42)
for i in range(41):
    work[i] = inp[i] ^ inp[i + 1]
enc = bytes(w ^ k for w, k in zip(work, ks))
print('re-enc matches ct[0:41]:', enc[:41] == ct[:41])
print('enc:', enc.hex())
print('ct :', ct.hex())
