import subprocess, difflib, sys
f = sys.argv[1]
head = subprocess.run(["git", "show", "HEAD:" + f], capture_output=True).stdout.decode().splitlines(keepends=True)
cur = open(f, newline="").read().splitlines(keepends=True)
s = lambda L: [l.rstrip("\r\n") for l in L]
out = []
for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, s(head), s(cur), autojunk=False).get_opcodes():
    out += head[a1:a2] if op == "equal" else [l.rstrip("\r\n") + "\r\n" for l in cur[b1:b2]]
open(f, "w", newline="").write("".join(out))
