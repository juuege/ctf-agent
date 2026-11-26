"""Mini C-SKY v2 disassembler from binutils csky-opc.h tables. Usage: csky_dis.py [func ...]"""
import os, re, struct, sys

HDR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'csky-opc-tables.h')
src = open(HDR, encoding='utf8', errors='replace').read()

# --- 1. OPRND_MASK defs ---
masks = {'NONE': 0}
for n, v in re.findall(r'#\s*define\s+OPRND_MASK_([0-9_]+)\s+(0x[0-9a-fA-F]+)', src):
    masks[n] = int(v, 0)
for n, v in re.findall(r'#\s*define\s+OPRND_MASK_([0-9_]+or[0-9_]+)\s+([^\n]*)', src):
    total = 0
    for part in re.findall(r'OPRND_MASK_([0-9_]+)', v):
        total |= masks[part]
    masks[n] = total

# --- 2. v1 table section (ck610, all 16-bit) ---
i2 = src.find('const struct csky_opcode csky_v2_opcodes')
tbl = src[:i2] if i2 > 0 else src

def split_args(s):
    args, depth, cur = [], 0, ''
    for ch in s:
        if ch == '(': depth += 1; cur += ch
        elif ch == ')': depth -= 1; cur += ch
        elif ch == ',' and depth == 0:
            args.append(cur.strip()); cur = ''
        else: cur += ch
    if cur.strip(): args.append(cur.strip())
    return args

def parse_opcode_info(text):
    m = re.match(r'S?OPCODE_INFO(?:_LIST|\d)?\s*\((.*)\)$', text, re.S)
    if not m: return None
    body = m.group(1)
    v = re.match(r'(0x[0-9a-fA-F]+)\s*,', body)
    if not v: return None
    val = int(v.group(1), 16)
    rest = body[v.end():]
    oprnds = []
    for om in re.finditer(r'\((NONE|[0-9_]+or[0-9_]+|[0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*,\s*OPRND_SHIFT_(\d)_BIT\)', rest):
        oprnds.append((om.group(1), om.group(2), int(om.group(3))))
    return val, oprnds

# --- 3. all entries in v2 section (any macro kind) ---
lut = []
for m in re.finditer(r'([A-Z][A-Z0-9_]*[A-Z0-9])\s*\(\s*"', tbl):
    kind = m.group(1)
    depth = 1; j = m.end()
    while depth:
        if tbl[j] == '(': depth += 1
        elif tbl[j] == ')': depth -= 1
        j += 1
    inner = tbl[m.end():j-1]
    args = split_args(inner)
    mnem = args[0].strip('"')
    # skip ISA conditionals that disable v2? kinds with V1 belong to v1 tables (not in slice)
    for a in args[1:]:
        if not re.match(r'S?OPCODE_INFO', a): continue
        r = parse_opcode_info(a)
        if not r: continue
        val, ops = r
        if val >= 0x10000: continue  # v1 FPU/32-bit oddities, skip
        lut.append((2, val, ops, mnem, kind))

def field_val(inst, bitspec):
    mk = masks[bitspec]
    if mk == 0: return 0, 0, 0
    parts = re.split(r'or', bitspec)
    ranges = []
    for rng in parts:
        sub = masks[rng]
        l = (sub & -sub).bit_length() - 1
        ranges.append((l, sub))
    ranges.sort()
    # single contiguous?
    if len(ranges) == 1:
        l, sub = ranges[0]
        return (inst & sub) >> l, l, sub
    val = 0
    for l, sub in ranges:  # binutils ORs shifted subfields: low range = low part
        val |= (inst & sub) >> l
    # order: for 'a_bor c_d' binutils places first-listed as low bits
    return val, ranges[0][0], mk

def decode(inst, ln):
    hits = []
    for L, val, ops, mnem, kind in lut:
        if L != ln: continue
        om = 0
        for bitspec, typ, sh in ops: om |= masks.get(bitspec, 0)
        m = ~om & ((1 << (ln * 8)) - 1)
        if m and (inst & m) == (val & m):
            hits.append((mnem, ops, kind))
    return hits

def render(inst, mnem, ops):
    parts = []
    for bitspec, typ, sh in ops:
        v, low, mk = field_val(inst, bitspec)
        if typ in ('NONE',): continue
        if typ == 'DUMMY_REG': parts.append('_'); continue
        if 'GREG' in typ or typ == 'AREG':
            parts.append(f'r{v}')
        elif 'BRACKET' in typ:
            parts.append(f'(r{v})')
        elif 'WITH_LSHIFT' in typ:
            parts.append(f'(r{v}<<{sh})')
        elif typ.startswith('IMM') or typ.startswith('OIMM') or typ.startswith('IMM'):
            parts.append(f'#{v << sh}' if sh else f'#{v}')
        elif 'OFF' in typ or 'COND' in typ:
            parts.append(f'@off{v << sh}')
        elif 'LRW' in typ:
            parts.append(f'[pool{v << sh}]')
        elif typ == 'CTRLREG':
            parts.append(f'cr{v}')
        elif 'REGLIST' in typ or 'PSR' in typ or 'HELPER' in typ or 'CONST' in typ or 'DUP' in typ:
            continue
        else:
            parts.append(f'{typ}[{v}]')
    return f'{mnem} ' + ', '.join(parts)

def disasm(data, base):
    out = []
    pc = 0
    while pc < len(data):
        hw0 = struct.unpack_from('<H', data, pc)[0]
        inst, ln = hw0, 2
        addr = base + pc
        hits = decode(inst, ln)
        if not hits:
            out.append(f'{addr:#x}: {inst:0{ln*2}x}  <unknown>')
        else:
            mnem, ops, kind = hits[0]
            extra = f'   [{len(hits)}:{",".join(h[0] for h in hits[1:5])}]' if len(hits) > 1 else ''
            out.append(f'{addr:#x}: {inst:0{ln*2}x}  ' + render(inst, mnem, ops) + extra)
        pc += ln
    return out

if __name__ == '__main__':
    binpath = os.environ.get('CSKY_BIN', '') or (sys.argv[1] if len(sys.argv) > 1 else '')
if not binpath:
    raise SystemExit('usage: set CSKY_BIN=<binary path> or pass the binary as argv[1]')
    b = open(binpath, 'rb').read()
    funcs = {'xor':(0x85d8,76),'swap':(0x8624,46),'rc4_init':(0x8654,170),'rc4_crypt':(0x870c,194),
             'check':(0x87dc,76),'cmp_result':(0x8828,82),'main':(0x8888,204)}
    want = sys.argv[1:] or list(funcs)
    for n in want:
        a, s = funcs[n]
        off = a - 0x8470 + 0x470
        print(f'== {n} ==')
        print('\n'.join(disasm(b[off:off+s], a)))
        print()

