#!/usr/bin/env python3
"""StockSentry: deterministic, read-only research triage for Base B20 reference data."""
import argparse, datetime as dt, decimal, hashlib, html, json, os, pathlib, re, sys, tempfile
import urllib.request, urllib.parse
from decimal import Decimal as D
API="https://api.coinbase.com/v1/tokenized-stocks"
DOC="https://docs.base.org/sdks/tokenized-stocks/overview"
MAX_PAGE=524288
MAX_PAGES=8
MAX_FILE=8*1024*1024
ADDR=re.compile(r"0x[0-9a-fA-F]{40}\Z")
decimal.getcontext().prec=96
def sha(b): return hashlib.sha256(b).hexdigest()
def fail(message): raise ValueError(message)
def stamp(v):
    if not isinstance(v,str) or len(v)>50: fail("invalid timestamp")
    try: t=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    except ValueError: fail("invalid timestamp")
    if t.tzinfo is None: fail("timestamp requires timezone")
    return t.astimezone(dt.timezone.utc)
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def number(v, positive=False):
    if isinstance(v,bool) or not isinstance(v,(str,int,D)) or len(str(v))>100: fail("invalid decimal")
    try: n=D(v)
    except decimal.InvalidOperation: fail("invalid decimal")
    if not n.is_finite() or n<0 or n>D("1e30") or (positive and n==0) or (n and n.adjusted() < -30): fail("decimal out of range")
    return n
def text(v,limit=240):
    if not isinstance(v,str) or len(v)>limit: fail("invalid text")
    return v
def address(v):
    if not isinstance(v,str) or not ADDR.fullmatch(v) or int(v[2:],16)==0: fail("invalid token address")
    return v.lower()
def dumps(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2,default=lambda n: str(n) if isinstance(n,D) else fail("unsupported JSON"))
def loads(raw):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: fail("duplicate JSON key")
            out[k]=v
        return out
    return json.loads(raw,parse_float=D,parse_constant=lambda _:fail("nonfinite JSON"),object_pairs_hook=pairs)
def read(path):
    with open(path,"rb") as f: raw=f.read(MAX_FILE+1)
    if len(raw)>MAX_FILE: fail("file exceeds 8MiB")
    return loads(raw)
def atomic(path,value):
    p=pathlib.Path(path)
    raw=(dumps(value)+"\n").encode()
    if len(raw)>MAX_FILE: fail("output exceeds 8MiB")
    fd,tmp=tempfile.mkstemp(prefix=".stocksentry-",dir=p.parent)
    try:
        with os.fdopen(fd,"wb") as f: f.write(raw);f.flush();os.fsync(f.fileno())
        os.link(tmp,p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def url(cursor=""):
    q={"page_size":"100","include_zero_supply":"true"}
    if cursor: q["page_token"]=cursor
    return API+"?"+urllib.parse.urlencode(q)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): fail("HTTP redirect refused")
def get(u):
    if not u.startswith(API+"?"): fail("unexpected API URL")
    req=urllib.request.Request(u,headers={"User-Agent":"StockSentry/0.1 read-only research","Accept":"application/json"})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(req,timeout=15) as r:
        if r.status!=200: fail("API not successful")
        if r.headers.get_content_type()!="application/json": fail("API not JSON")
        raw=r.read(MAX_PAGE+1)
        if len(raw)>MAX_PAGE: fail("page exceeds 512KiB")
    return raw
def token(v):
    if not isinstance(v,dict): fail("invalid token record")
    a=address(v.get("contract_address"))
    dec=v.get("decimals",0)
    if type(dec) is not int or not 0<=dec<=36: fail("invalid decimals")
    paused=v.get("paused_features",[])
    if not isinstance(paused,list) or len(paused)>64 or any(type(x) is not int or x<0 for x in paused): fail("invalid pause flags")
    m=number(v.get("multiplier"),True)
    supply=number(v.get("total_supply",0))
    nav=number(v["nav_price"],True) if "nav_price" in v else None
    ts=stamp(v["nav_price_updated_at"]).isoformat() if "nav_price_updated_at" in v else None
    return {"address":a,"symbol":text(v.get("symbol",""),80),"name":text(v.get("name","")),
        "decimals":dec,"isin":text(v.get("isin",""),40),"adgm_isin":text(v.get("adgm_isin",""),40),
        "token_supply":str(supply),"multiplier":str(m),"share_equivalent_supply":str(supply*m),
        "nav":str(nav) if nav is not None else None,"nav_at":ts,"paused_features":paused}
def validate(s):
    if not isinstance(s,dict) or s.get("schema")!="stocksentry.snapshot.v1" or s.get("chain_id")!=8453 or s.get("source")!=API: fail("snapshot identity mismatch")
    captured=stamp(s.get("captured_at"))
    if s.get("complete") is not True: fail("incomplete snapshot")
    pages=s.get("pages")
    if not isinstance(pages,list) or not 1<=len(pages)<=MAX_PAGES: fail("invalid page count")
    cursor="";seen=set();rows={};receipts={}
    for i,p in enumerate(pages):
        if not isinstance(p,dict) or p.get("url")!=url(cursor): fail("pagination URL mismatch")
        at=stamp(p.get("received_at"))
        if not captured-dt.timedelta(minutes=5)<=at<=captured: fail("page capture time mismatch")
        raw=p.get("raw")
        if not isinstance(raw,str): fail("missing page body")
        b=raw.encode()
        if len(b)>MAX_PAGE or sha(b)!=p.get("sha256"): fail("page SHA mismatch")
        data=loads(raw)
        if not isinstance(data,dict) or not isinstance(data.get("tokens",[]),list) or len(data.get("tokens",[]))>500: fail("invalid API page")
        for item in data.get("tokens",[]):
            t=token(item);a=t["address"]
            if a in rows: fail("duplicate token across pages")
            rows[a]=t;receipts[a]={"page_sha256":sha(b),"page":i+1,"url":p["url"]}
        cursor=data.get("next_page_token","")
        if not isinstance(cursor,str) or len(cursor)>4096: fail("invalid page cursor")
        if cursor and cursor in seen: fail("repeated page cursor")
        seen.add(cursor)
        if (not cursor)!=(i==len(pages)-1): fail("pagination chain incomplete")
    return captured,rows,receipts
def capture(fetch=get,clock=now):
    pages=[];cursor="";seen=set()
    for _ in range(MAX_PAGES):
        u=url(cursor);raw=fetch(u)
        if not isinstance(raw,bytes) or len(raw)>MAX_PAGE: fail("page exceeds 512KiB")
        body=raw.decode("utf-8");data=loads(body)
        if not isinstance(data,dict): fail("invalid API page")
        pages.append({"url":u,"received_at":clock(),"raw":body,"sha256":sha(raw)})
        cursor=data.get("next_page_token","")
        if not isinstance(cursor,str) or len(cursor)>4096: fail("invalid page cursor")
        if not cursor: break
        if cursor in seen: fail("repeated page cursor")
        seen.add(cursor)
    else: fail("page budget exhausted; no complete snapshot")
    s={"schema":"stocksentry.snapshot.v1","chain_id":8453,"source":API,"captured_at":clock(),"complete":True,"pages":pages}
    validate(s)
    return s
def analyze(s,previous=None,holdings=None,at=None,max_age=3600):
    captured,rows,evidence=validate(s)
    when=stamp(at or now())
    if captured>when+dt.timedelta(seconds=60): fail("future snapshot")
    if type(max_age) is not int or not 1<=max_age<=604800: fail("invalid freshness policy")
    old={};prior=None
    if previous is not None:
        prior,old,_=validate(previous)
        if prior>=captured: fail("previous snapshot is not earlier")
    positions={}
    if holdings is not None:
        if not isinstance(holdings,dict) or len(holdings)>4000: fail("invalid holdings")
        for a,n in holdings.items():
            a=address(a)
            if a in positions: fail("duplicate holding")
            positions[a]=number(n)
    findings=[]
    def add(a,code,reason,priority=2):
        findings.append({"address":a,"code":code,"reason":reason,"priority":priority,"evidence":evidence.get(a),
                         "research_action":ACTION.get(code,"Review the source record before relying on it.")})
    stale_snapshot=(when-captured).total_seconds()>max_age
    if stale_snapshot: add(None,"snapshot_stale","Snapshot exceeds the selected freshness policy; no position valuation.",0)
    output=[];total=D(0);valued=0
    for a,t in sorted(rows.items()):
        t=dict(t);nav=D(t["nav"]) if t["nav"] is not None else None
        ts=stamp(t["nav_at"]) if t["nav_at"] else None
        age=(when-ts).total_seconds() if ts else None
        valid_price=not stale_snapshot and nav is not None and age is not None and 0<=age<=max_age
        if nav is None: add(a,"nav_missing","No usable NAV/reference value was published.",0)
        elif ts is None: add(a,"nav_time_missing","NAV has no timestamp; freshness is unknown.",0)
        elif age<0: add(a,"nav_future","NAV timestamp is in the future.",0)
        elif age>max_age: add(a,"nav_stale",f"NAV is {int(age)} seconds old; policy is {max_age}s. Off-market retention is possible, not proof of feed failure.",1)
        if t["paused_features"]: add(a,"paused","Issuer reports paused feature flags: "+str(t["paused_features"]),1)
        if not t["isin"] or not t["adgm_isin"]: add(a,"identifier_missing","Underlying or tokenized-security ISIN is absent; they are different identifiers.",3)
        if previous is not None and a not in old: add(a,"new_record","New address in a complete observation; not proof of new deployment.",3)
        if a in old:
            before=old[a]
            if D(t["multiplier"])!=D(before["multiplier"]):
                change=(D(t["multiplier"])/D(before["multiplier"])-1)*100
                add(a,"multiplier_changed",f'Multiplier {before["multiplier"]} -> {t["multiplier"]} ({change}%); investigate corporate action, not trading return.',1)
            for field in ["decimals","isin","adgm_isin"]:
                if t[field]!=before[field]: add(a,"identity_changed",f"{field} changed; revalidate identifier and unit mapping.",0)
            if (t["symbol"],t["name"])!=(before["symbol"],before["name"]): add(a,"metadata_changed","Name or symbol changed at the same address.",3)
        qty=positions.get(a)
        t.update(nav_age_seconds=age,reference_usable=valid_price,evidence=evidence[a],holding_token_units=str(qty) if qty is not None else None,
                 holding_share_equivalent=str(qty*D(t["multiplier"])) if qty is not None else None,
                 holding_reference_usd=str(qty*nav) if qty is not None and valid_price else None)
        if qty is not None and valid_price: total+=qty*nav;valued+=1
        output.append(t)
    for a in sorted(set(old)-set(rows)): add(a,"record_missing","Absent from this complete snapshot; not proof of burn, delisting or a zero balance.",2)
    for a in sorted(set(positions)-set(rows)): add(a,"holding_unknown","Holding address absent from reference data; value excluded.",0)
    findings.sort(key=lambda x:(x["priority"],x["address"] or "",x["code"]))
    return {"schema":"stocksentry.research.v1","chain_id":8453,"captured_at":s["captured_at"],"analyzed_at":when.isoformat(),
        "snapshot_sha256":sha(dumps(s).encode()),"source":API,"policy_max_age_seconds":max_age,
        "origin":"Captured public API data; not independently verified onchain or cryptographic provenance.",
        "agent":"Deterministic bounded discovery/validation/prioritization/research-action pipeline. No LLM or paid API.",
        "rows":output,"findings":findings,"previous_captured_at":previous.get("captured_at") if previous else None,
        "holdings":{"provided":len(positions),"valued":valued,"complete_reference_coverage":bool(positions) and valued==len(positions),
                    "partial_reference_usd":str(total) if valued else None,"pnl":None},
        "limits":["NAV is not an executable bid or ask.","Multiplier already applied in API NAV; do not apply twice to token valuation.",
                  "Supply is normalized token units, not raw wei or shares.","No trading, custody, mint/redeem, wallet access or eligibility determination.",
                  "A locally editable SHA envelope proves replay consistency, not source authenticity.","No investment recommendation or implied shareholder rights."]}
ACTION={"nav_stale":"Check market hours/holidays and latest official feed timestamp before using NAV.",
"multiplier_changed":"Compare issuer announcement and dated corporate-action record; recompute share equivalents once.",
"paused":"Inspect official pause scope; do not infer transfer eligibility from an allowance.",
"identity_changed":"Reconcile the same contract address against official B20 metadata and identifiers.",
"holding_unknown":"Confirm chain and exact contract address; never join holdings by ticker.",
"snapshot_stale":"Capture a fresh complete source snapshot; retain the earlier receipt.",
"record_missing":"Recheck the official list and captured pagination; do not replace absence with zero."}
def render(r):
    esc=lambda v:html.escape(str(v),quote=True)
    td=lambda v:f"<td>{esc(v if v is not None else 'Unavailable')}</td>"
    body=""
    for t in r["rows"]:
        body+=f'<tr><th scope="row">{esc(t["symbol"])}<small>{esc(t["address"])}</small></th>'+ "".join(td(t[k]) for k in ["token_supply","multiplier","share_equivalent_supply","nav","nav_at","holding_reference_usd"])+"</tr>"
    findings="".join(f'<details><summary>{esc(f["code"])} · {esc(f["address"] or "All records")}</summary><p>{esc(f["reason"])}</p><p>{esc(f["research_action"])}</p><pre>{esc(dumps(f["evidence"]))}</pre></details>' for f in r["findings"])
    limits="".join(f"<li>{esc(x)}</li>" for x in r["limits"])
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>StockSentry — B20 research</title><style>
*{box-sizing:border-box}body{margin:0;background:#f6f6f3;color:#192724;font:16px/1.55 system-ui,sans-serif}main{max-width:1250px;margin:auto;padding:32px 22px}h1{font-size:2.4rem;margin:0}h2{margin-top:2rem}p{max-width:90ch}.table-wrap{max-inline-size:100%;max-block-size:65vh;overflow:auto;border:1px solid #bac9c2}table{border-collapse:separate;border-spacing:0;min-width:100%;font-size:.9rem}th,td{padding:14px;text-align:left;border-bottom:1px solid #cad4cf;white-space:nowrap;font-variant-numeric:tabular-nums}thead th{position:sticky;top:0;background:#e5eee9;z-index:2}tbody th{position:sticky;left:0;background:#f6f6f3;max-width:215px;white-space:normal;z-index:1}small{display:block;font-size:.7rem;overflow-wrap:anywhere}caption{text-align:left;padding:12px}details{border-bottom:1px solid #bac9c2;padding:14px 0}summary{cursor:pointer;overflow-wrap:anywhere;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem}a{color:#00594c}a:focus-visible,summary:focus-visible,.table-wrap:focus-visible{outline:3px solid #ba561b;outline-offset:3px}li{margin:.5em 0}@media(max-width:500px){main{padding:22px 14px}h1{font-size:2rem}th,td{padding:10px}}
</style><main><h1>StockSentry</h1><p>What changed in Base tokenized stocks, and what needs checking before using the numbers?</p>""" +f"""
<p>Captured {esc(r["captured_at"])} · {len(r["rows"])} records · {len(r["findings"])} research checks.<br>
Freshness policy: {esc(r["policy_max_age_seconds"])} seconds. Reference values are not executable prices.</p>
<p>{esc(r["origin"])}</p><h2 id="table-title">Token units, share equivalents and reference values</h2>
<div class="table-wrap" role="region" aria-labelledby="table-title" tabindex="0"><table><caption>Base chain 8453 — address is the identity, not ticker</caption><thead><tr><th scope="col">Token / address</th><th scope="col">Supply: tokens</th><th scope="col">Multiplier</th><th scope="col">Supply: share equivalents</th><th scope="col">NAV: USD/token</th><th scope="col">NAV timestamp</th><th scope="col">Holding: reference USD</th></tr></thead><tbody>{body}</tbody></table></div>
<h2>Research queue</h2>{findings or "<p>No checks triggered under this policy; this does not establish investment quality or legal eligibility.</p>"}
<h2>Coverage and limits</h2><p>{esc(dumps(r["holdings"]))}</p><ul>{limits}</ul>
<p><a href="{DOC}" rel="noreferrer">Official API interpretation</a> · <a href="{API}" rel="noreferrer">Public reference API</a></p>
<details><summary>Replay evidence</summary><p>Snapshot SHA-256: {esc(r["snapshot_sha256"])}</p><p>{esc(r["agent"])}</p></details></main></html>"""
def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest="op",required=True)
    c=sub.add_parser("capture");c.add_argument("--out",required=True)
    a=sub.add_parser("analyze");a.add_argument("snapshot");a.add_argument("--previous");a.add_argument("--holdings");a.add_argument("--at");a.add_argument("--max-age",type=int,default=3600);a.add_argument("--out",required=True);a.add_argument("--html")
    args=ap.parse_args()
    try:
        if args.op=="capture":
            if pathlib.Path(args.out).exists(): fail("output already exists; use a new receipt path")
            s=capture();atomic(args.out,s);print(dumps({"captured":args.out,"records":len(validate(s)[1]),"source":API}));return
        inputs=[pathlib.Path(p).resolve() for p in [args.snapshot,args.previous,args.holdings] if p]
        outputs=[pathlib.Path(p).resolve() for p in [args.out,args.html] if p]
        if set(inputs)&set(outputs) or len(outputs)!=len(set(outputs)): fail("output cannot overwrite an input or another output")
        r=analyze(read(args.snapshot),read(args.previous) if args.previous else None,read(args.holdings) if args.holdings else None,args.at,args.max_age)
        atomic(args.out,r)
        if args.html:
            markup=render(r)
            if len(markup.encode())>MAX_FILE: fail("HTML exceeds 8MiB")
            pathlib.Path(args.html).write_text(markup)
        print(dumps({"records":len(r["rows"]),"checks":len(r["findings"]),"out":args.out,"html":args.html,"pnl":None}))
    except (ValueError,OSError,KeyError,TypeError,decimal.DecimalException) as e:
        print(dumps({"error":str(e)}),file=sys.stderr);return 2
if __name__=="__main__":sys.exit(main())
