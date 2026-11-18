import glob,os,collections
for p in glob.glob('C:/Users/Administrator/.local/share/TeleAgent/TeleAgent的工作空间/CTFPilot-v0.1/tests/challs/06_zip_nested/**/*', recursive=True):
    if os.path.isfile(p):
        d=open(p,'rb').read()
        if len(d)>32:
            c=collections.Counter(d).most_common(5)
            print(p,'top bytes',[(hex(b),n) for b,n in c])
