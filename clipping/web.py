"""Local dashboard. Runs the pipeline, shows the results, pushes them to Google Sheets.

Deliberately localhost-only: it holds a live API key and writes to a real spreadsheet, so
it is not something to expose. The key never reaches the browser.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from clipping import pipeline, sheets, store
from clipping.config import ConfigError, load_campaigns, load_settings
from clipping.providers import BATCH_SIZE, by_key

app = FastAPI(title="Clipping")

DEFAULT_FIXTURE = "tests/fixtures/scraper_2025/tagged.json"


class RunRequest(BaseModel):
    mode: str = "fixture"          # "fixture" replays a saved page and costs nothing
    target: str | None = None      # overrides BRAND_HANDLE, so any account can be tracked
    max_profiles: int = 5
    batch_size: int = BATCH_SIZE
    cursor: str | None = None
    provider: str = "scraper_20251"


@app.get("/api/state")
def state() -> JSONResponse:
    try:
        settings = load_settings()
        campaigns = load_campaigns()
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    conn = store.connect(settings.db_path)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("posts", "creators", "metrics", "runs")
    }
    totals = store.campaign_totals(conn)
    conn.close()

    return JSONResponse({
        "brand": settings.brand_handle,
        "keys": len(settings.rapidapi_keys),
        "db_path": settings.db_path,
        "counts": counts,
        "campaigns": [{"id": c.id, "name": c.name, "hashtags": sorted(c.hashtags)}
                      for c in campaigns],
        "campaign_totals": totals,
        "fixture_available": Path(DEFAULT_FIXTURE).exists(),
        "sheets_configured": bool(settings.google_sheet_id),
        "service_account_email": sheets.service_account_email(
            settings.google_service_account_json or ""
        ),
    })


@app.get("/api/posts")
def posts() -> JSONResponse:
    settings = load_settings()
    conn = store.connect(settings.db_path)
    rows = store.report_rows(conn)
    conn.close()
    return JSONResponse({"rows": rows})


@app.post("/api/run")
def run(req: RunRequest) -> JSONResponse:
    try:
        settings = load_settings()
        campaigns = load_campaigns()
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    spec = by_key(req.provider)
    if spec is None:
        return JSONResponse({"error": f"unknown provider {req.provider}"}, status_code=400)

    fixture = DEFAULT_FIXTURE if req.mode == "fixture" else None
    if fixture and not Path(fixture).exists():
        return JSONResponse(
            {"error": f"No saved page at {fixture}. Run doctor --save-fixtures first."},
            status_code=400,
        )

    try:
        result = pipeline.ingest(
            settings, campaigns, spec,
            target=req.target,
            fixture=fixture,
            max_profiles=req.max_profiles,
            batch_size=req.batch_size,
            cursor=req.cursor,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return JSONResponse(result.as_dict())


@app.post("/api/export")
def export() -> JSONResponse:
    settings = load_settings()
    conn = store.connect(settings.db_path)
    try:
        summary = sheets.push(conn, settings.google_sheet_id or "",
                              settings.google_service_account_json or "",
                              settings.google_oauth_client_json)
    except sheets.SheetsError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        conn.close()
    return JSONResponse(summary)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clipping</title>
<style>
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#14171a; --muted:#6b7280; --line:#e3e6ea;
  --accent:#1f6feb; --warn:#b45309; --warnbg:#fef3c7; --err:#b42318; --errbg:#fee4e2;
  --ok:#087443; --okbg:#d1fae5;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0f1216; --card:#171b21; --ink:#e6e9ef; --muted:#9aa4b2; --line:#262c35;
  --accent:#4c8dff; --warnbg:#3a2d10; --warn:#fbbf24; --errbg:#3a1d1c; --err:#fca5a5;
  --okbg:#0f3226; --ok:#6ee7b7;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,Segoe UI,Roboto,'Malgun Gothic',sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);background:var(--card);
  display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:650;letter-spacing:-.01em}
.brand{color:var(--muted);font-weight:400}
.spacer{flex:1}
.wrap{padding:20px 24px;max-width:1400px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px;margin-bottom:16px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
button{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:9px 16px;
  font-size:13px;font-weight:600;cursor:pointer}
button.sec{background:transparent;color:var(--ink);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:not-allowed}
select,input{background:var(--bg);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:8px 10px;font-size:13px}
input[type=search]{min-width:220px}
.stats{display:flex;gap:26px;flex-wrap:wrap}
.stat b{display:block;font-size:21px;font-weight:650;letter-spacing:-.02em}
.stat span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:9px 10px;border-bottom:2px solid var(--line);
  color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;
  cursor:pointer;white-space:nowrap;user-select:none}
th:hover{color:var(--ink)}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:var(--bg)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.nw{white-space:nowrap}
a{color:var(--accent)}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;
  font-weight:600;border:1px solid var(--line)}
.Nano{background:#eef2ff;color:#3730a3}.Micro{background:#ecfdf5;color:#065f46}
.Mid{background:#fff7ed;color:#9a3412}.Macro{background:#fdf2f8;color:#9d174d}
.Mega{background:#eff6ff;color:#1e40af}
@media (prefers-color-scheme:dark){
 .Nano{background:#1e1b4b;color:#c7d2fe}.Micro{background:#064e3b;color:#a7f3d0}
 .Mid{background:#431407;color:#fed7aa}.Macro{background:#500724;color:#fbcfe8}
 .Mega{background:#172554;color:#bfdbfe}}
.unattributed{color:var(--muted);font-style:italic}
.msg{padding:11px 14px;border-radius:8px;margin-bottom:14px;font-size:13px}
.msg.err{background:var(--errbg);color:var(--err)}
.msg.ok{background:var(--okbg);color:var(--ok)}
.msg.warn{background:var(--warnbg);color:var(--warn)}
.tags{color:var(--muted);font-size:11px;max-width:260px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.muted{color:var(--muted);font-size:12px}
.scroll{overflow-x:auto}
</style></head><body>
<header>
  <h1>Clipping <span class="brand" id="brand"></span></h1>
  <div class="spacer"></div>
  <div class="muted" id="quota"></div>
</header>
<div class="wrap">
  <div id="banner"></div>

  <div class="card">
    <div class="row">
      <div><label>Target account</label><br>
        <input id="target" type="text" placeholder="username" style="width:190px"></div>
      <div><label>Source</label><br>
        <select id="mode">
          <option value="fixture">Saved page (free)</option>
          <option value="live">Live API (spends quota)</option>
        </select></div>
      <div><label>Batch size</label><br>
        <input id="batch" type="number" min="1" max="50" value="20" style="width:90px"></div>
      <div><label>Creators to tier</label><br>
        <input id="maxp" type="number" min="1" max="50" value="5" style="width:90px"></div>
      <div style="align-self:flex-end"><button id="runBtn">Run</button></div>
      <div class="spacer"></div>
      <div style="align-self:flex-end">
        <button class="sec" id="exportBtn">Export to Google Sheets</button></div>
    </div>
    <div class="muted" style="margin-top:10px" id="costHint"></div>
  </div>

  <div class="card"><div class="stats" id="stats"></div></div>

  <div class="card">
    <div class="row" style="margin-bottom:12px">
      <input type="search" id="q" placeholder="Search creator or hashtag">
      <select id="tierF"><option value="">All tiers</option></select>
      <select id="campF"><option value="">All campaigns</option></select>
      <div class="spacer"></div><div class="muted" id="count"></div>
    </div>
    <div class="scroll"><table>
      <thead><tr>
        <th data-k="creator">Creator</th><th data-k="tier">Tier</th>
        <th data-k="followers" class="num">Followers</th>
        <th data-k="campaign">Campaign</th>
        <th data-k="likes" class="num">Likes</th>
        <th data-k="comments" class="num">Comments</th>
        <th data-k="hashtags">Hashtags</th>
        <th data-k="posted_at">Posted</th><th>Link</th>
      </tr></thead><tbody id="tbody"></tbody>
    </table></div>
  </div>
</div>
<script>
let rows=[], sortK='likes', sortDir=-1;
const $=id=>document.getElementById(id);
const fmt=n=>(n==null?'':Number(n).toLocaleString());

function banner(text,kind){ $('banner').innerHTML =
  text ? '<div class="msg '+kind+'">'+text+'</div>' : ''; }

async function load(){
  const s = await (await fetch('/api/state')).json();
  if(s.error){ banner(s.error,'err'); return; }
  $('brand').textContent = '@'+s.brand;
  if(!$('target').value) $('target').placeholder = s.brand;   // default, still editable
  $('stats').innerHTML = [
    ['posts',s.counts.posts],['creators',s.counts.creators],
    ['metric rows',s.counts.metrics],['runs',s.counts.runs]
  ].map(([k,v])=>'<div class="stat"><b>'+fmt(v)+'</b><span>'+k+'</span></div>').join('');
  if(!s.sheets_configured){
    $('exportBtn').disabled=true;
    $('exportBtn').title='Set GOOGLE_SHEET_ID in .env';
  }
  const camps = s.campaigns.map(c=>c.id);
  $('campF').innerHTML = '<option value="">All campaigns</option>' +
    camps.concat(['unattributed']).map(c=>'<option>'+c+'</option>').join('');
  await loadPosts();
}

async function loadPosts(){
  const d = await (await fetch('/api/posts')).json();
  rows = d.rows||[]; draw();
}

function draw(){
  const q=$('q').value.toLowerCase(), t=$('tierF').value, c=$('campF').value;
  let view = rows.filter(r =>
    (!t||r.tier===t) && (!c||r.campaign===c) &&
    (!q || (r.creator+' '+r.hashtags).toLowerCase().includes(q)));
  view.sort((a,b)=>{const x=a[sortK],y=b[sortK];
    return (typeof x==='number'? x-y : String(x).localeCompare(String(y)))*sortDir;});
  $('count').textContent = view.length+' of '+rows.length+' posts';
  const tiers=[...new Set(rows.map(r=>r.tier))];
  if($('tierF').options.length===1)
    $('tierF').innerHTML='<option value="">All tiers</option>'+
      tiers.map(x=>'<option>'+x+'</option>').join('');
  $('tbody').innerHTML = view.map(r=>`<tr>
    <td>@${r.creator}</td>
    <td><span class="pill ${r.tier}">${r.tier}</span></td>
    <td class="num">${fmt(r.followers)}</td>
    <td class="${r.campaign==='unattributed'?'unattributed':''}">${r.campaign}</td>
    <td class="num">${fmt(r.likes)}</td>
    <td class="num">${fmt(r.comments)}</td>
    <td class="tags" title="${r.hashtags}">${r.hashtags||'-'}</td>
    <td class="nw">${r.posted_at}</td>
    <td class="nw"><a href="${r.url}" target="_blank" rel="noopener">open</a></td></tr>`).join('')
    || '<tr><td colspan="9" class="muted">No posts yet. Press Run.</td></tr>';
}

document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; sortDir = (k===sortK)? -sortDir : -1; sortK=k; draw();});
['q','tierF','campF'].forEach(id=>$(id).oninput=draw);

$('mode').onchange=()=>{
  const live=$('mode').value==='live';
  $('costHint').textContent = live
    ? 'Live mode spends 1 call for the tagged page plus 1 per creator tiered.'
    : 'Replays the saved page. Tiering creators still spends 1 call each.';
};
$('mode').onchange();

let nextCursor = null;

$('runBtn').onclick=async()=>{
  const live=$('mode').value==='live', n=+$('maxp').value;
  const tgt=$('target').value.trim().replace(/^@/,'');
  const cost = (live?1:0)+n;
  // The newline below is double-escaped on purpose. This page lives inside a Python
  // string, so a single escape would become a real line break and split the literal.
  if(!confirm('Target: @'+(tgt||$('target').placeholder)
    +'\\nThis run will spend about '+cost+' API call(s). Continue?')) return;
  $('runBtn').disabled=true; $('runBtn').textContent='Running...'; banner('','');
  try{
    const r = await (await fetch('/api/run',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:$('mode').value, max_profiles:n,
        target:tgt||null, batch_size:+$('batch').value, cursor:nextCursor})})).json();
    if(r.error){ banner(r.error,'err'); }
    else{
      nextCursor = r.next_cursor || null;
      let m = 'Target @'+r.target+': found '+r.discovered+' posts, tiered '+r.tiered
        + ' creators, stored ' + r.stored + ' rows. Spent '+r.calls+' call(s).';
      if(r.skipped) m += ' '+r.skipped+' creators skipped for lack of a profile lookup.';
      banner(m, r.skipped? 'warn':'ok');
      if(r.quota_remaining!=null)
        $('quota').textContent='API quota left: '+r.quota_remaining+
          (r.quota_limit? ' / '+r.quota_limit : '');
      await load();
    }
  }catch(e){ banner(String(e),'err'); }
  $('runBtn').disabled=false; $('runBtn').textContent='Run';
};

$('exportBtn').onclick=async()=>{
  $('exportBtn').disabled=true; $('exportBtn').textContent='Exporting...';
  try{
    const r = await (await fetch('/api/export',{method:'POST'})).json();
    if(r.error) banner(r.error,'err');
    else banner('Wrote '+r.posts+' posts to "'+r.title+'". '
      +'<a href="'+r.url+'" target="_blank" rel="noopener">Open the sheet</a>','ok');
  }catch(e){ banner(String(e),'err'); }
  $('exportBtn').disabled=false; $('exportBtn').textContent='Export to Google Sheets';
};

load();
</script></body></html>
"""
