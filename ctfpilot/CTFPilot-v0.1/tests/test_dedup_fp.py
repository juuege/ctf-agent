"""Dup-4th-hole regression set -- run with the project python:

    python -X utf8 tests/test_dedup_fp.py [--force]

Covers (engine/dedup-attach-fingerprint-v1, chain entry 10):
  - every verified same-challenge repackaging pair must hit is_dup_dir
  - distinct challenges sharing only environment files (libc, docker
    boilerplate) must NOT hit (FP_EXCLUDE: libc*/ld-*, ctf.xinetd,
    docker-entrypoint.sh, deploy/flag/readme/...)
  - full-tree pair scan of ctf-feed: content-fingerprint collisions
    must be exactly the known same-challenge set (catches both false
    negatives in dedup and new template files needing FP_EXCLUDE)
  - constructed re-packaging is blocked; constructed fresh content passes

--force rebuilds the fps index (slow: md5s the whole ctf-feed tree);
the default fast path relies on load_index() self-seeding the fps
section when missing.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dedup as D  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
FEED = os.path.join(WS, "ctf-feed")
SCRATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       ".temp-dedup-fp")

# Verified same-challenge pairs (hand-verified / md5-verified; the live
# qwqsncx accident pair first).
KNOWN_SAME = {
    frozenset(("2024-qwqsncx", "2024-qwqsncx__勒索病毒事件排查")),
    frozenset(("2024-qwqsncx", "2024-qwqsncx__机床故障排查")),   # fan.pcapng
    frozenset(("2024-CCB-CISCN-Quals__avm", "CTF-Archives__2024-CCB-CISCN-Quals__avm")),
    frozenset(("2023-Bluehat-Quals__reverse-瓷砖计算机", "2023-Bluehat-Quals__瓷砖计算机")),
    frozenset(("2023-zjsdxs__Misc-Ez_Signin", "CTF-Archives_2023-zjsdxs__Misc-Ez_Signin")),
    frozenset(("2025-LongjianCup-Finals__babyRSA", "CTF-Archives_2025-LongjianCup-Finals__2.babyRSA")),
    frozenset(("2025-jksn-final__编码的迷宫", "2025-jksn-final__逆向的钥匙")),  # reverse-1.exe
    frozenset(("2026-NCO-Quals__PWN-入门夺旗-parrotbooth", "CTF-Archives__2026-NCO-Quals__PWN-入门夺旗-parrotbooth")),
    frozenset(("2026-NCO-Quals__Web-中等夺旗-个性签名生成器", "2026-NCO-Quals__个性签名生成器")),
    frozenset(("2026-NCO-Quals__Web-入门夺旗-超级无敌保险箱", "2026-NCO-Quals__超级无敌保险箱")),
    frozenset(("2024-dfjk-Misc", "2024-dfjk__简历")),               # default.a
    frozenset(("2025-jksn-quals__MORSE", "2025-jksn-quals-attachments726")),  # shenmi.wav
    frozenset(("2025-jswa-misc2", "2025-jswa__misc2")),             # uuid.png
    # evolve-11 round (2026-11-16 23:2x): 12 more md5-verified same-
    # content pairs surfaced by the full-tree pair scan -- whole-repo /
    # merged-bag pulls vs their component singles (zip-inner folding
    # collides them exactly as designed), plus one cross-name
    # republication and one source-side mislabeled bundle.
    frozenset(("2023-NISA2023-capabilities__capabilities",
               "CTF-Archives_NSSCTF-Round12-ability__ability")),     # repo.zip inners
    frozenset(("2024-ShuXinCup-sjaq-西部赛区", "2024-ShuXinCup-sjaq__BabyRSA")),
    frozenset(("2024-ShuXinCup-sjaq-西部赛区", "2024-ShuXinCup-sjaq__Backpack")),
    frozenset(("2024-chb__Another_leak_of_LCG", "CTF-Archives__2024-chb__Mypow")),  # Mypow zip in LCG dir
    frozenset(("2024-newstar__Since_you_know_something", "2024-newstar__crypto")),
    frozenset(("2024-newstar__crypto", "2024-newstar__one_and_more")),
    frozenset(("2024-newstar__crypto", "2024-newstar__xor")),
    frozenset(("2024-newstar__crypto", "2024-newstar__一眼秒了")),
    frozenset(("2024-newstar__crypto", "2024-newstar__疑惑")),
    frozenset(("2025-SDNISC-haixuan", "2025-SDNISC-haixuan__Crypto-ezcrypto")),
    frozenset(("2025-SDNISC-haixuan", "2025-SDNISC-haixuan__Crypto-hardcrypto")),
    frozenset(("2025-SDNISC-haixuan", "2025-SDNISC-haixuan__Crypto-rsaaa")),
}
KNOWN_SAME_IDS = {tuple(sorted(p)) for p in KNOWN_SAME}

FAILURES = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name +
           ("  " + detail if detail else ""))
    if not cond:
        FAILURES.append(name)


def main():
    idx, _ipath = D.load_index(force=("--force" in sys.argv))
    n_fps = len(idx.get("fps") or {})
    check("index has fps section", n_fps > 0, "fps=%d" % n_fps)

    # positives
    for a, b in sorted(KNOWN_SAME_IDS):
        ok = ""
        for cand, other in ((a, b), (b, a)):
            dcand = os.path.join(FEED, cand)
            if not os.path.isdir(dcand):
                continue
            hit, why = D.is_dup_dir(idx, dcand)
            if hit and other in why:
                ok = why
        check("pos[%s ~ %s]" % (a, b), bool(ok), ok)

    # negatives: distinct challenges sharing only environment files
    for a, b in (("2022-mtgxs-pwn-smtp", "2022-xhlj-pwn-message_board"),
                 ("2025-WQB-Quals__digtal_bomb", "2025-WQB-Quals__ood_canary"),
                 ("2023-hgame-week1-pwn-simple_shellcode-src",
                  "2023-hgame-week2-pwn-editable_note-src"),
                 ("2022-mtgxs-pwn-smtp", "2024-dfjk-Misc"),
                 ("2024-qwqsncx", "2022-mtgxs-pwn-smtp")):
        hit, why = D.is_dup_dir(idx, os.path.join(FEED, b))
        fp = bool(hit and a in (why or ""))
        check("neg[%s vs %s]" % (a, b), not fp, why if fp else "")

    # full-tree pair scan
    dirs = {}
    for n in sorted(os.listdir(FEED)):
        full = os.path.join(FEED, n)
        if os.path.isdir(full) and ("__" in n or n[:4].isdigit()):
            s = set(D.dir_fps(full))
            if s:
                dirs[n] = s
    names = sorted(dirs)
    unexpected = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if dirs[a] & dirs[b] and ((a, b) not in KNOWN_SAME_IDS
                                       and (b, a) not in KNOWN_SAME_IDS):
                unexpected.append((a, b))
    for a, b in unexpected:
        print("       UNEXPECTED collision: %s ~ %s" % (a, b))
    check("pair-scan-clean (%d dirs)" % len(names), not unexpected)

    # constructed re-pull block / fresh pass
    if os.path.isdir(SCRATCH):
        shutil.rmtree(SCRATCH, ignore_errors=True)
    os.makedirs(SCRATCH)
    src = os.path.join(FEED, "2022-mtgxs-pwn-smtp")
    replica = os.path.join(SCRATCH, "totally-different-name")
    os.makedirs(replica)
    for n in ("pwn", "super.conf"):
        p = os.path.join(src, n)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(replica, n))
    hit, why = D.is_dup_dir(idx, replica)
    check("constructed-replica-blocked", hit and "2022-mtgxs-pwn-smtp" in why, why)
    fresh = os.path.join(SCRATCH, "brand-new-challenge")
    os.makedirs(fresh)
    with open(os.path.join(fresh, "unique.bin"), "wb") as f:
        f.write(b"never-seen-before-payload")
    hit2, why2 = D.is_dup_dir(idx, fresh)
    check("constructed-fresh-passes", not hit2, why2)
    shutil.rmtree(SCRATCH, ignore_errors=True)

    if FAILURES:
        print("FAILED: %d (%s)" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("dedup-fp regression: ALL PASS")


if __name__ == "__main__":
    main()
