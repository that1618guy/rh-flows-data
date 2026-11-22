import json, datetime, collections, os
DATA=os.environ.get('DATA_DIR','data/latest')
UTC=datetime.UTC
import os
# cut-off = the day the data was pulled (that day is partial), not the day this script runs
today=datetime.date.fromisoformat(os.environ['FETCH_DATE']) if os.environ.get('FETCH_DATE') else datetime.datetime.now(UTC).date()
F=json.load(open(DATA+'/rh_dailyFees.json'))['protocols']
R={p['name']:p for p in json.load(open(DATA+'/rh_dailyRevenue.json'))['protocols']}
HR={p['name']:p for p in json.load(open(DATA+'/rh_dailyHoldersRevenue.json'))['protocols']}
P={p['name']:p for p in json.load(open(DATA+'/protocols.json'))}
# RH-native projects: a project (its versions grouped under one parent) is native if most of its all-time fees were
# earned on Robinhood Chain. Then all of its Robinhood Chain activity counts, whatever other chains it also lists.
SKIP=('Robinhood Chain','Arbitrum Nitro')
def parent(p): return p.get('parentProtocol') or p['name']
rh_all=collections.Counter()
for p in F:
    if p['name'] not in SKIP: rh_all[parent(p)]+=p.get('totalAllTime') or 0
try:
    every_all=collections.Counter()
    for p in json.load(open(DATA+'/all_fees.json'))['protocols']:
        every_all[parent(p)]+=p.get('totalAllTime') or 0
except (OSError, ValueError, KeyError):
    every_all=None                             # without every chain's fees, fall back to "runs only on Robinhood Chain"
groups=collections.OrderedDict()
for p in F:
    if p['name'] not in SKIP: groups.setdefault(parent(p),[]).append(p)
def is_native(key, versions):
    if rh_all[key] <= 0: return False          # never earned a fee on Robinhood Chain: nothing to be native by
    if every_all is None:                      # no all-chain data today: fall back to "runs only on Robinhood Chain"
        return all((v.get('chains') or [])==['Robinhood Chain'] for v in versions)
    return rh_all[key] > 0.5*every_all.get(key, 0)
agg=collections.OrderedDict()
for key, versions in groups.items():
    if not is_native(key, versions): continue
    for p in versions:
        n=p['name']
        a=agg.setdefault(key,{'names':[],'cat':p.get('category'),'fees':0,'rev':0,'hold':0,'holdKnown':False,'revKnown':False,'tvl':0,'prev':0})
        a['names'].append(n); a['fees']+=p.get('total30d') or 0; a['prev']+=p.get('total60dto30d') or 0
        a['rev']+=R.get(n,{}).get('total30d') or 0
        if R.get(n,{}).get('total30d') is not None: a['revKnown']=True
        h=HR.get(n,{}).get('total30d')
        if h is not None: a['hold']+=h; a['holdKnown']=True
        a['tvl']+=((P.get(n,{}).get('chainTvls') or {}).get('Robinhood Chain') or 0)
rows=sorted(agg.items(),key=lambda kv:-kv[1]['fees'])
json.dump(rows,open(DATA+'/rh_native.json','w'))
# weekly fees by category: 12 complete weeks, ending on the last complete day
c=json.load(open(DATA+'/rh_fees_chart.json'))
cat={p['name']:p.get('category') for p in F}
def grp(c_):
    if c_=='Launchpad': return 'Launchpads'
    if c_ in ('Dexs','DEX Aggregator'): return 'DEXs'
    if c_ in ('Trading App','Telegram Bot','Interface'): return 'Trading apps'
    if c_=='Derivatives': return 'Perps'
    if c_=='Cross Chain Bridge': return 'Bridges'
    return 'Other'
days=[(datetime.datetime.fromtimestamp(ts,UTC).date(),br) for ts,br in c['totalDataChartBreakdown']]
days=[x for x in days if x[0]<today]          # drop today's partial day
# bars are complete Monday-to-Sunday weeks, so they line up with the weekly read
_end=days[-1][0]; _sun=_end-datetime.timedelta(days=(_end.weekday()+1)%7)
wdays=[x for x in days if x[0]<=_sun]
last=wdays[-84:]
weeks=[]
for w in range(12):
    chunk=last[w*7:(w+1)*7]; s=collections.Counter()
    for d,br in chunk:
        for name,v in br.items(): s[grp(cat.get(name))]+=v
    weeks.append((chunk[0][0].isoformat(),dict(s)))
t=json.load(open(DATA+'/rh_tvl.json'))
# DefiLlama dates each TVL point at 00:00 UTC, so a point dated D is the close of D-1 (today's point is live).
# Label points by the day they close and stop at the last full day, so TVL lines up with every other number.
_close=[(datetime.datetime.fromtimestamp(x['date'],UTC).date()-datetime.timedelta(days=1),x['tvl']) for x in t]
tv=[(d.isoformat(),v) for d,v in _close if d<=days[-1][0]][-91:]
json.dump({'weeks':weeks,'tvl':tv,'last_full_day':days[-1][0].isoformat(),'fetched':today.isoformat()},open(DATA+'/rh_series.json','w'))
print("native projects:",len(rows),"| weeks:",weeks[0][0],"->",weeks[-1][0],"(last full day",days[-1][0],") | tvl:",tv[0][0],"->",tv[-1][0])
for k,a in rows[:22]:
    sh=a['hold']/a['fees']*100 if a['fees'] else 0
    print(f"{k:28s} {str(a['cat']):22s} fees={a['fees']/1e3:9.1f}k prev={a['prev']/1e3:9.1f}k hold={a['hold']/1e3:8.1f}k ({sh:6.1f}%) known={a['holdKnown']} tvl={a['tvl']/1e6:6.2f}M")
print("week totals ($M):",[round(sum(w[1].values())/1e6,2) for w in weeks])
