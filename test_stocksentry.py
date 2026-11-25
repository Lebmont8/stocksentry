import copy,datetime as dt,hashlib,json,pathlib,tempfile,unittest
from decimal import Decimal as D
import stocksentry as s
AT="2026-11-24T20:00:00+00:00"
A="0x"+"1"*40;B="0x"+"2"*40
def rec(**kw):
    return dict(contract_address=A,symbol="EXAMPLE",name="Synthetic test equity",decimals=18,total_supply="100",multiplier="1.02",nav_price="51",nav_price_updated_at=AT,isin="US123",adgm_isin="AE123",**kw)
def fixture(rows=None,at=AT):
    raw=json.dumps({"tokens":rows if rows is not None else [rec()]}).encode()
    return s.capture(lambda _:raw,lambda:at)
def reseal(snap):
    for p in snap["pages"]:p["sha256"]=s.sha(p["raw"].encode())
    return snap
class Tests(unittest.TestCase):
    def run_analysis(self,ss=None,**kw):return s.analyze(ss or fixture(),at=AT,**kw)
    def test_exact_share_supply_and_no_double_nav(self):
        r=self.run_analysis(holdings={A:"2"})
        self.assertEqual(r["rows"][0]["share_equivalent_supply"],"102.00")
        self.assertEqual(r["rows"][0]["holding_share_equivalent"],"2.04")
        self.assertEqual(r["holdings"]["partial_reference_usd"],"102")
        self.assertIsNone(r["holdings"]["pnl"])
    def test_missing_supply_is_zero(self):
        row=rec();del row["total_supply"]
        self.assertEqual(s.validate(fixture([row]))[1][A]["token_supply"],"0")
    def test_missing_nav_does_not_become_zero(self):
        row=rec();del row["nav_price"]
        r=self.run_analysis(fixture([row]),holdings={A:"2"})
        self.assertIsNone(r["holdings"]["partial_reference_usd"])
    def test_missing_nav_time_blocks_valuation(self):
        row=rec();del row["nav_price_updated_at"]
        self.assertFalse(self.run_analysis(fixture([row]))["rows"][0]["reference_usable"])
    def test_stale_nav(self):
        row=rec();row["nav_price_updated_at"]="2026-11-23T20:00:00Z"
        r=self.run_analysis(fixture([row]),holdings={A:"2"})
        self.assertEqual(r["findings"][0]["code"],"nav_stale")
        self.assertIsNone(r["holdings"]["partial_reference_usd"])
    def test_future_nav(self):
        row=rec();row["nav_price_updated_at"]="2026-11-25T20:00:00Z"
        self.assertFalse(self.run_analysis(fixture([row]))["rows"][0]["reference_usable"])
    def test_snapshot_stale_blocks_all_prices(self):
        r=s.analyze(fixture(),at="2026-11-26T20:00:00Z",holdings={A:"2"})
        self.assertEqual(r["findings"][0]["code"],"snapshot_stale")
        self.assertIsNone(r["holdings"]["partial_reference_usd"])
    def test_partial_coverage_not_total_portfolio(self):
        r=self.run_analysis(holdings={A:"2",B:"1"})["holdings"]
        self.assertEqual((r["provided"],r["valued"],r["complete_reference_coverage"]),(2,1,False))
    def test_empty_holdings_not_full_coverage(self):
        self.assertFalse(self.run_analysis()["holdings"]["complete_reference_coverage"])
    def test_multiplier_change_not_profit(self):
        old=fixture(at="2026-11-24T19:00:00Z")
        row=rec();row["multiplier"]="2.04"
        r=self.run_analysis(fixture([row]),previous=old)
        f=[x for x in r["findings"] if x["code"]=="multiplier_changed"][0]
        self.assertIn("not trading return",f["reason"])
    def test_same_ticker_different_addresses_not_joined(self):
        r=rec();r["contract_address"]=B
        self.assertEqual(len(s.validate(fixture([rec(),r]))[1]),2)
    def test_metadata_and_identifier_changes(self):
        old=fixture(at="2026-11-24T19:00:00Z");r=rec();r.update(symbol="NEW",isin="US456")
        codes=[x["code"] for x in self.run_analysis(fixture([r]),previous=old)["findings"]]
        self.assertIn("metadata_changed",codes);self.assertIn("identity_changed",codes)
    def test_absent_not_zero_balance(self):
        old=fixture(at="2026-11-24T19:00:00Z")
        r=self.run_analysis(fixture([]),previous=old)
        self.assertIn("not proof",r["findings"][0]["reason"])
    def test_paused_flag_and_missing_isin(self):
        r=rec();r.update(paused_features=[1],adgm_isin="")
        codes=[x["code"] for x in self.run_analysis(fixture([r]))["findings"]]
        self.assertIn("paused",codes);self.assertIn("identifier_missing",codes)
    def test_tamper(self):
        snap=fixture();snap["pages"][0]["raw"]+=" "
        with self.assertRaisesRegex(ValueError,"SHA"):s.validate(snap)
    def test_wrong_chain_and_source(self):
        for k,v in [("chain_id",1),("source","https://evil.example"),("complete",False)]:
            snap=fixture();snap[k]=v
            with self.assertRaises(ValueError):s.validate(snap)
    def test_page_url_tamper(self):
        snap=fixture();snap["pages"][0]["url"]="https://evil.example"
        with self.assertRaisesRegex(ValueError,"URL"):s.validate(snap)
    def test_duplicate_addresses_across_pages(self):
        raw=json.dumps({"tokens":[rec()],"next_page_token":"x"}).encode()
        raw2=json.dumps({"tokens":[rec()]}).encode();it=iter([raw,raw2])
        with self.assertRaisesRegex(ValueError,"duplicate token"):s.capture(lambda _:next(it),lambda:AT)
    def test_real_pagination_queries(self):
        r=rec();r["contract_address"]=B
        bodies=iter([json.dumps({"tokens":[rec()],"next_page_token":"a&b"}).encode(),json.dumps({"tokens":[r]}).encode()])
        urls=[]
        def fetch(u):urls.append(u);return next(bodies)
        self.assertEqual(len(s.validate(s.capture(fetch,lambda:AT))[1]),2)
        self.assertIn("page_token=a%26b",urls[1]);self.assertIn("include_zero_supply=true",urls[1])
    def test_cursor_loop(self):
        raw=b'{"tokens":[],"next_page_token":"same"}'
        with self.assertRaisesRegex(ValueError,"repeated"):s.capture(lambda _:raw,lambda:AT)
    def test_max_pages_fail_closed(self):
        it=iter(range(50))
        with self.assertRaisesRegex(ValueError,"budget"):s.capture(lambda _:json.dumps({"tokens":[],"next_page_token":str(next(it))}).encode(),lambda:AT)
    def test_partial_snapshot_rejected(self):
        snap=fixture();p=snap["pages"][0];p["raw"]=json.dumps({"tokens":[rec()],"next_page_token":"unfinished"});reseal(snap)
        with self.assertRaisesRegex(ValueError,"incomplete"):s.validate(snap)
    def test_oversize_page(self):
        with self.assertRaisesRegex(ValueError,"512"):s.capture(lambda _:b"x"*(s.MAX_PAGE+1),lambda:AT)
    def test_duplicate_json_keys_and_nonfinite(self):
        for raw in ['{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}']:
            with self.assertRaises(ValueError):s.loads(raw)
    def test_invalid_numbers(self):
        for n in ["NaN","Infinity","-1","1e1000","1e-1000",True,None,1.1]:
            with self.subTest(n=n),self.assertRaises(ValueError):s.number(n)
    def test_bad_identity_fields(self):
        for field,value in [("contract_address","0x"+"0"*40),("contract_address","abc"),("decimals",-1),("decimals",True),("multiplier","0"),("nav_price","0"),("paused_features",["1"]),("symbol","x"*81)]:
            r=rec();r[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):fixture([r])
    def test_naive_timestamp(self):
        with self.assertRaisesRegex(ValueError,"timezone"):s.stamp("2026-11-24T20:00:00")
    def test_previous_later_rejected(self):
        with self.assertRaisesRegex(ValueError,"earlier"):self.run_analysis(previous=fixture())
    def test_future_snapshot_rejected(self):
        with self.assertRaisesRegex(ValueError,"future"):s.analyze(fixture(),at="2026-11-23T20:00:00Z")
    def test_freshness_bounds(self):
        for age in [-1,0,True,604801]:
            with self.subTest(age=age),self.assertRaises(ValueError):self.run_analysis(max_age=age)
    def test_raw_decimal_precision(self):
        raw=b'{"tokens":[{"contract_address":"'+A.encode()+b'","multiplier":1.000000000000000001,"total_supply":1.000000000000000001}]}'
        row=next(iter(s.validate(s.capture(lambda _:raw,lambda:AT))[1].values()))
        self.assertEqual(row["share_equivalent_supply"],"1.000000000000000002000000000000000001")
    def test_html_escapes_untrusted_text(self):
        r=rec();r["symbol"]='<img src=x onerror="alert(1)">'
        markup=s.render(self.run_analysis(fixture([r])))
        self.assertNotIn("<img",markup);self.assertIn("&lt;img",markup)
        self.assertIn("Content-Security-Policy",markup);self.assertNotIn("<script",markup)
    def test_atomic_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/"s.json";s.atomic(p,fixture());self.assertEqual(len(s.validate(s.read(p))[1]),1)
    def test_no_redirect_or_custom_endpoint(self):
        with self.assertRaisesRegex(ValueError,"redirect"):s.NoRedirect().redirect_request(None,None,None,None,None,None)
        with self.assertRaisesRegex(ValueError,"unexpected"):s.get("https://evil.example")
    def test_stale_page_capture(self):
        snap=fixture();snap["pages"][0]["received_at"]="2026-11-20T20:00:00Z"
        with self.assertRaisesRegex(ValueError,"capture"):s.validate(snap)

    def test_equivalent_multiplier_format_not_change(self):
        old=fixture(at="2026-11-24T19:00:00Z");r=rec();r["multiplier"]="1.020"
        result=self.run_analysis(fixture([r]),previous=old)
        self.assertNotIn("multiplier_changed",[x["code"] for x in result["findings"]])
    def test_write_once_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/"s.json";s.atomic(p,fixture());before=p.read_bytes()
            with self.assertRaises(FileExistsError):s.atomic(p,{"bad":"replacement"})
            self.assertEqual(p.read_bytes(),before)

if __name__=="__main__":unittest.main(verbosity=2)
