import sys,json,cryptography
sys.path.insert(0,".")
from app.crypto import *
import app.crypto as c
K="ab"*32; cls=c.KeyCipher
x=cls(K); f="/workspace/verify-r2/p10r2/old_ct.json"
if sys.argv[1]=="gen":
    ct,n=x.encrypt("sk-legacy-key-1234","provider:1"); json.dump({"ct":ct.hex(),"n":n.hex(),"v":cryptography.__version__},open(f,"w")); print("gen with",cryptography.__version__)
else:
    d=json.load(open(f)); assert x.decrypt(bytes.fromhex(d["ct"]),bytes.fromhex(d["n"]),"provider:1")=="sk-legacy-key-1234"
    ct,n=x.encrypt("sk-new-5678","provider:2"); assert x.decrypt(ct,n,"provider:2")=="sk-new-5678"
    try: x.decrypt(ct,n,"provider:3"); print("AAD NOT ENFORCED")
    except Exception: pass
    print("decrypt old(",d["v"],") with",cryptography.__version__,"OK; roundtrip OK; aad enforced")
