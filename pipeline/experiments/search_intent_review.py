"""Generate a standalone blind search-review page; never read the treatment key."""
from __future__ import annotations

import argparse
from copy import deepcopy
import ipaddress
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx

CONTRACT = "frozen-search-intent-comparison-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_MEDIA = re.compile(r"/media/(?:keyframe/[A-Za-z0-9_-]+/[0-9]{1,4}|preview/[A-Za-z0-9_-]+)\Z")
_FIELDS = ("film_title", "caption", "matched_text", "matched_text_view")
MAX_PLAYBACK_FILMS, MAX_PLAYBACK_BYTES, PLAYBACK_SECONDS = 200, 8192, 60.0


def loopback_base(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname
    try:
        local = host == "localhost" or ipaddress.ip_address(host or "").is_loopback
        port = parsed.port
    except ValueError:
        local, port = False, None
    if (not local or parsed.scheme not in {"http", "https"} or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query
            or parsed.fragment or (port is not None and port < 1)):
        raise ValueError("API base must be an HTTP(S) loopback origin without credentials, path or query")
    return value.rstrip("/")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def resolve_playback_urls(film_ids: list[str], api_base: str) -> dict:
    """Read bounded resolver metadata once per film; never prepare or fetch video."""
    base, films = loopback_base(api_base), sorted(set(film_ids))
    if len(films) > MAX_PLAYBACK_FILMS or any(not re.fullmatch(r"[a-f0-9]{64}", f) for f in films):
        raise ValueError("Playback resolution needs at most 200 valid film IDs")
    result, deadline = {}, time.monotonic() + PLAYBACK_SECONDS
    with httpx.Client(follow_redirects=False, trust_env=False, timeout=5.0) as client:
        for film_id in films:
            result[film_id] = {"status": "unresolved", "url": None, "reason": "Resolution time budget exhausted"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            try:
                with client.stream("GET", base + f"/video/{film_id}/playback", timeout=min(5.0, remaining)) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > MAX_PLAYBACK_BYTES or time.monotonic() > deadline:
                            raise ValueError("Resolver response exceeded its size or time budget")
                        body.extend(chunk)
                data = json.loads(body)
                url = data.get("url") if isinstance(data, dict) else None
                if not isinstance(url, str) or not re.fullmatch(rf"/video/{film_id}(?:\?representation=[a-f0-9]{{24}})?", url):
                    raise ValueError("Resolver returned an unexpected source URL")
                result[film_id] = {"status": "resolved", "url": url,
                                   "kind": "prepared" if "?" in url else "original"}
            except (httpx.HTTPError, ValueError) as exc:
                result[film_id]["reason"] = str(exc)[:300]
    return result


def validate_review(data: dict) -> dict:
    """Whitelist blind fields, source authority and bounded document sizes."""
    if not isinstance(data, dict) or data.get("contract") != CONTRACT:
        raise ValueError("Expected a frozen blind-review document")
    if not re.fullmatch(r"[a-f0-9]{64}", str(data.get("input_sha256", ""))):
        raise ValueError("Blind review needs its frozen input SHA256")
    cases = data.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("Blind review needs 1–100 cases")
    clean, identities = [], set()
    for case in cases:
        if not isinstance(case, dict) or not _ID.fullmatch(str(case.get("id", ""))) or case["id"] in identities:
            raise ValueError("Case IDs must be unique, bounded identifiers")
        identities.add(case["id"])
        if not isinstance(case.get("query"), str) or not case["query"].strip() or len(case["query"]) > 4000:
            raise ValueError(f"Invalid query in {case['id']}")
        scope = case.get("film_ids", [])
        if not isinstance(scope, list) or any(not re.fullmatch(r"[a-f0-9]{64}", str(item)) for item in scope):
            raise ValueError(f"Invalid film scope in {case['id']}")
        lists = case.get("lists")
        if not isinstance(lists, dict) or set(lists) != set("ABCD"):
            raise ValueError(f"Case {case['id']} needs blind lists A, B, C and D")
        out, grades = {}, {}
        for label, rows in lists.items():
            if not isinstance(rows, list) or len(rows) > 12:
                raise ValueError("Each blind list must contain at most 12 rows")
            out[label] = []
            for row in rows:
                if not isinstance(row, dict) or not _ID.fullmatch(str(row.get("unit_id", ""))) or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("film_id", ""))):
                    raise ValueError(f"Invalid source identity in {case['id']}/{label}")
                start, end = row.get("t_start"), row.get("t_end")
                if not (_number(start) and _number(end) and 0 <= start < end):
                    raise ValueError("Source intervals must have finite ordered bounds")
                result = {key: row[key] for key in ("unit_id", "film_id", "t_start", "t_end")}
                for field in _FIELDS:
                    value = row.get(field)
                    if value is not None and (not isinstance(value, str) or len(value) > 20000):
                        raise ValueError(f"Invalid source text: {field}")
                    result[field] = value
                for field in ("keyframe_url", "matched_frame_url", "preview_url"):
                    value = row.get(field)
                    if value is not None and (not isinstance(value, str) or not _MEDIA.fullmatch(value)
                            or value.split("/")[3] != row["unit_id"]
                            or (field == "preview_url") != value.startswith("/media/preview/")):
                        raise ValueError(f"Unsafe or mismatched local media route: {field}")
                    result[field] = value
                timestamp = row.get("matched_frame_timestamp", row.get("timestamp"))
                if timestamp is not None and not (_number(timestamp) and start <= timestamp < end):
                    raise ValueError("Matched frame timestamp must lie inside its source interval")
                grade = row.get("human_grade")
                if grade is not None and (isinstance(grade, bool) or not isinstance(grade, int) or not 0 <= grade <= 3):
                    raise ValueError("Human grades must be null or integers 0–3")
                key = (row["film_id"], row["unit_id"])
                if grade is not None and key in grades and grades[key] != grade:
                    raise ValueError("The same source has conflicting human grades across lists")
                if grade is not None:
                    grades[key] = grade
                result.update(matched_frame_timestamp=timestamp, human_grade=grade, rank=len(out[label]) + 1)
                out[label].append(result)
        preference, notes = case.get("preferred_list"), case.get("notes")
        if preference not in [None, *"ABCD"] or (notes is not None and (not isinstance(notes, str) or len(notes) > 20000)):
            raise ValueError("Invalid human preference or notes")
        status = case.get("human_review_status", "pending")
        if status not in {"pending", "in-progress", "reviewed"}:
            raise ValueError("Invalid human review status")
        clean.append({"id": case["id"], "query": case["query"], "film_ids": scope, "lists": out,
                      "human_review_status": status, "preferred_list": preference, "notes": notes})
    limits = data.get("limits", "Human review only; no automatic relevance grades.")
    if not isinstance(limits, str) or len(limits) > 10000:
        raise ValueError("Review limits must be bounded text")
    return {"contract": CONTRACT, "input_sha256": data["input_sha256"], "cases": clean, "limits": limits}


_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#101113;color:#ededf0;font:15px/1.45 system-ui,sans-serif}main{max-width:1440px;margin:auto;padding:28px}header{display:flex;align-items:center;gap:16px;flex-wrap:wrap}h1{font-size:23px;margin:0;flex:1}h2{font-size:21px;line-height:1.4;margin:22px 0 6px}p{margin:8px 0}.muted,small{color:#a7a8b0}.error{color:#ffb8a8}.toolbar,.review,.column-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0}button,select,textarea{font:inherit;color:inherit;background:#222429;border:1px solid #42444c;border-radius:8px;padding:8px 12px}button{cursor:pointer}button:hover{border-color:#acb4ec}button:disabled{opacity:.35;cursor:default}button:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid #b0bbff;outline-offset:2px}#query-select{flex:1;min-width:180px;max-width:900px}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}.column-head{position:sticky;top:0;background:#101113;padding:10px 0;z-index:1}article{border:1px solid #31333b;border-radius:12px;overflow:hidden;background:#191b20;margin-bottom:18px}article img{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#08090a}.card-body{padding:14px}.card-title{font-weight:600;display:flex;justify-content:space-between;gap:12px}.actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px}details{margin-top:12px;color:#b8b9c1}summary{cursor:pointer}textarea{width:100%;min-height:75px;resize:vertical}dialog{width:min(1000px,92vw);padding:18px;background:#17191e;color:#eee;border:1px solid #4c4f59;border-radius:12px}dialog::backdrop{background:#000b}video{display:block;width:100%;max-height:72vh;margin:12px 0;background:#000}#notice{min-height:22px}.review{border-top:1px solid #333;padding-top:18px}label{display:flex;align-items:center;gap:8px}@media(max-width:650px){main{padding:16px}.columns{gap:10px}.card-body{padding:9px}.actions{align-items:stretch;flex-direction:column}h2{font-size:18px}}
"""

_SCRIPT = r"""
function createReview(data, storage) {
  const key = 'scene-recall:search-intent-review:' + data.input_sha256;
  const state = {input_sha256:data.input_sha256,grades:Object.create(null),preferences:Object.create(null),notes:Object.create(null),reviewed:Object.create(null),index:0,left:'A',right:'B',limit:5};
  const unitKey = (id,row) => id + ':' + row.film_id + ':' + row.unit_id;
  let warning = storage?'':'Local saving is unavailable. Export JSON to preserve your work.';
  for (const c of data.cases) { for (const rows of Object.values(c.lists)) for (const row of rows) { const k=unitKey(c.id,row); if (!(k in state.grades) || row.human_grade != null) state.grades[k]=row.human_grade; } state.preferences[c.id]=c.preferred_list;state.notes[c.id]=c.notes;state.reviewed[c.id]=c.human_review_status==='reviewed'; }
  try { const raw=storage?.getItem(key);if(raw){if(raw.length>2000000)throw Error('too large');const saved=JSON.parse(raw);if(saved.input_sha256!==data.input_sha256)throw Error('wrong input');for(const k of Object.keys(state.grades)){const v=saved.grades?.[k];if(v===null || Number.isInteger(v)&&v>=0&&v<=3)state.grades[k]=v;}for(const c of data.cases){const p=saved.preferences?.[c.id],n=saved.notes?.[c.id];if(p===null||['A','B','C','D'].includes(p))state.preferences[c.id]=p;if(n===null||typeof n==='string'&&n.length<=20000)state.notes[c.id]=n;state.reviewed[c.id]=saved.reviewed?.[c.id]===true;}if(Number.isInteger(saved.index)&&saved.index>=0&&saved.index<data.cases.length)state.index=saved.index;for(const side of ['left','right'])if(['A','B','C','D'].includes(saved[side]))state[side]=saved[side];if([5,12].includes(saved.limit))state.limit=saved.limit;}}
  catch(e){warning='Saved progress could not be read. Review in memory and export your work.';}
  function save(){try{if(!storage)throw Error('unavailable');storage.setItem(key,JSON.stringify(state));}catch(e){warning='Local saving is unavailable. Export JSON to preserve your work.';}}
  function grade(id,row,value){if(value!==null&&(!Number.isInteger(value)||value<0||value>3))throw Error('Invalid grade');state.grades[unitKey(id,row)]=value;save();}
  function exportData(){const out=JSON.parse(JSON.stringify(data));for(const c of out.cases){let changed=false;for(const rows of Object.values(c.lists))for(const row of rows){row.human_grade=state.grades[unitKey(c.id,row)]??null;changed ||= row.human_grade!==null;}c.preferred_list=state.preferences[c.id]??null;c.notes=state.notes[c.id]||null;c.human_review_status=state.reviewed[c.id]?'reviewed':changed||c.preferred_list||c.notes?'in-progress':'pending';}return out;}
  return {state,unitKey,save,grade,exportData,get warning(){return warning;}};
}
globalThis.createReview = createReview;
function startReview() {
  const payload=JSON.parse(document.getElementById('review-data').textContent),data=payload.review,base=payload.api_base,playback=payload.playback;
  let storage;try{storage=window.localStorage;}catch(e){}
  const review=createReview(data,storage),s=review.state,$=id=>document.getElementById(id);
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const time=n=>Math.floor(n/60)+':'+(n%60).toFixed(1).padStart(4,'0');
  const media=path=>typeof path==='string'&&/^\/media\/(keyframe\/[A-Za-z0-9_-]+\/[0-9]{1,4}|preview\/[A-Za-z0-9_-]+)$/.test(path)?base+path:null;
  const notice=()=>{$('notice').textContent=review.warning||'Progress saves in this browser. Export JSON before moving this file or changing browsers.';$('notice').className=review.warning?'error':'muted';};
  function option(select,value,text){const o=el('option',text);o.value=value;select.append(o);}
  for(const [i,c] of data.cases.entries())option($('query-select'),i,(i+1)+'. '+c.query.slice(0,95));
  for(const id of ['left','right','preferred']){if(id==='preferred')option($(id),'','No preference yet');for(const label of ['A','B','C','D'])option($(id),label,'List '+label);}
  const video=$('video'),dialog=$('player');let bounds=null;
  function stop(){video.pause();video.removeAttribute('src');video.load();bounds=null;}
  function close(){if(dialog.open)dialog.close();else stop();}
  dialog.addEventListener('close',stop);dialog.addEventListener('click',e=>{if(e.target===dialog)close();});$('close-player').onclick=close;
  video.addEventListener('loadedmetadata',()=>{if(bounds)video.currentTime=bounds.start;});
  video.addEventListener('seeking',()=>{if(bounds&&(video.currentTime<bounds.start||video.currentTime>bounds.end))video.currentTime=Math.max(bounds.start,Math.min(bounds.end,video.currentTime));});
  video.addEventListener('timeupdate',()=>{if(bounds&&video.currentTime>=bounds.end)video.pause();});
  video.addEventListener('error',()=>{$('player-note').textContent='Playback failed. Check API/source availability and browser codec support. A stale prepared URL requires regenerating this page with --resolve-playback.';});
  function play(row){const source=playback[row.film_id];if(source?.status!=='resolved')return;close();bounds={start:row.t_start,end:row.t_end};$('player-title').textContent=(row.film_title||row.film_id)+' · '+time(bounds.start)+'–'+time(bounds.end);$('player-note').textContent='Press Play to inspect this source interval. Playback stops at its end. '+(source.kind==='prepared'?'Prepared representation pinned when this page was generated.':'Original source; browser compatibility is unverified.');video.src=base+source.url+'#t='+bounds.start+','+bounds.end;dialog.showModal();}
  function card(row,index,c){const item=el('article'),src=media(row.matched_frame_url||row.keyframe_url);if(src){const image=el('img');image.src=src;image.loading='lazy';image.alt='Source frame from '+(row.film_title||row.film_id);image.onerror=()=>image.replaceWith(el('p','Thumbnail unavailable — check the local API.','error'));item.append(image);}else item.append(el('p','No source thumbnail supplied.','error'));
    const body=el('div',null,'card-body'),heading=el('div',null,'card-title');heading.append(el('span',row.film_title||row.film_id),el('small','#'+(index+1)));body.append(heading,el('small',time(row.t_start)+'–'+time(row.t_end)+(row.matched_frame_timestamp==null?'':' · frame '+time(row.matched_frame_timestamp))));
    const source=playback[row.film_id],actions=el('div',null,'actions'),button=el('button',source?.status==='resolved'?'Play source':'Playback unresolved');button.disabled=source?.status!=='resolved';button.onclick=()=>play(row);const label=el('label','Relevance'),select=el('select');select.dataset.unit=review.unitKey(c.id,row);select.setAttribute('aria-label','Relevance for '+(row.film_title||row.unit_id)+' result '+(index+1));for(const [v,text] of [['','Unrated'],['0','0 · Wrong'],['1','1 · Partial'],['2','2 · Good'],['3','3 · Strong']])option(select,v,text);select.value=s.grades[select.dataset.unit]??'';select.onchange=()=>{review.grade(c.id,row,select.value===''?null:Number(select.value));for(const other of document.querySelectorAll('select[data-unit]'))if(other.dataset.unit===select.dataset.unit)other.value=select.value;notice();};label.append(select);actions.append(button,label);body.append(actions);if(button.disabled)body.append(el('small',source?.reason||'Playback URL was not resolved.','error'));
    const details=el('details');details.append(el('summary','Show indexed description / text'),el('p',row.caption||'No caption supplied.'));if(row.matched_text)details.append(el('small','Matched '+(row.matched_text_view||'text')),el('p',row.matched_text));body.append(details);item.append(body);return item;}
  function render(){close();const c=data.cases[s.index];$('query-select').value=s.index;$('query').textContent=c.query;$('position').textContent=(s.index+1)+' / '+data.cases.length+' · '+(c.film_ids.length?'Explicit movie scope':'All movies');$('prev').disabled=s.index===0;$('next').disabled=s.index===data.cases.length-1;$('count').value=s.limit;for(const side of ['left','right']){$(side).value=s[side];const rows=c.lists[s[side]].slice(0,s.limit);$(side+'-rows').replaceChildren(...rows.map((row,i)=>card(row,i,c)));if(!rows.length)$(side+'-rows').append(el('p','No results in this list.','muted'));}$('preferred').value=s.preferences[c.id]??'';$('notes').value=s.notes[c.id]??'';$('reviewed').checked=s.reviewed[c.id];notice();}
  function move(index){s.index=index;review.save();render();window.scrollTo(0,0);}
  $('query-select').onchange=e=>move(Number(e.target.value));$('prev').onclick=()=>move(Math.max(0,s.index-1));$('next').onclick=()=>move(Math.min(data.cases.length-1,s.index+1));
  for(const side of ['left','right'])$(side).onchange=e=>{s[side]=e.target.value;review.save();render();};$('count').onchange=e=>{s.limit=Number(e.target.value);review.save();render();};
  $('preferred').onchange=e=>{s.preferences[data.cases[s.index].id]=e.target.value||null;review.save();notice();};$('notes').oninput=e=>{s.notes[data.cases[s.index].id]=e.target.value;review.save();notice();};$('reviewed').onchange=e=>{s.reviewed[data.cases[s.index].id]=e.target.checked;review.save();notice();};
  $('export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(review.exportData(),null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='search-intent-human-review-'+data.input_sha256.slice(0,12)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const resolved=Object.values(playback).filter(v=>v.status==='resolved').length;$('media-status').textContent='Playback URLs resolved: '+resolved+' / '+Object.keys(playback).length+'. '+(payload.resolve_playback?'URLs were pinned during generation; codec support still depends on your browser.':'Offline generation. Recreate with --resolve-playback to enable source playback.');$('limits').textContent=data.limits;window.addEventListener('beforeunload',()=>video.pause());render();
}
if(typeof document!=='undefined'){try{startReview();}catch(e){const error=document.createElement('p');error.className='error';error.textContent='Review data could not be loaded: '+e.message;document.body.replaceChildren(error);}}
"""


def render_review(data: dict, api_base: str = "http://localhost:8000", *, resolve_playback: bool = False) -> str:
    review, base = validate_review(deepcopy(data)), loopback_base(api_base)
    films = sorted({row["film_id"] for case in review["cases"] for rows in case["lists"].values() for row in rows})
    playback = (resolve_playback_urls(films, base) if resolve_playback else
                {f: {"status": "unresolved", "url": None, "reason": "Offline generation; use --resolve-playback"} for f in films})
    payload = json.dumps({"review": review, "api_base": base, "playback": playback,
                          "resolve_playback": resolve_playback}, ensure_ascii=True, allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Blind search comparison</title><style>""" + _STYLE + """</style>
<main><header><h1>Blind search comparison</h1><button id="export">Export JSON</button></header><p class="muted">Compare source evidence first. Grades follow the same scene across lists within each query.</p><p id="media-status" class="muted"></p>
<div class="toolbar"><button id="prev">Previous</button><select id="query-select" aria-label="Query"></select><button id="next">Next</button><label>Show <select id="count"><option value="5">Top 5</option><option value="12">Top 12</option></select></label></div>
<h2 id="query"></h2><p id="position" class="muted"></p><div class="columns"><section><div class="column-head"><label>Left <select id="left"></select></label></div><div id="left-rows"></div></section><section><div class="column-head"><label>Right <select id="right"></select></label></div><div id="right-rows"></div></section></div>
<div class="review"><label>Preferred <select id="preferred"></select></label><label><input id="reviewed" type="checkbox">Mark query reviewed</label></div><label for="notes">Review notes</label><textarea id="notes" maxlength="20000" placeholder="Missing details, convincing matches, or uncertainty…"></textarea><p id="notice"></p><details><summary>Comparison limits</summary><p id="limits" class="muted"></p></details></main>
<dialog id="player"><header><strong id="player-title"></strong><button id="close-player">Close</button></header><video id="video" controls preload="metadata" playsinline></video><p id="player-note" class="muted"></p></dialog>
<script id="review-data" type="application/json">""" + payload + "</script><script>" + _SCRIPT + "</script></html>"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New standalone HTML file; existing files are never replaced")
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--resolve-playback", action="store_true", help="Resolve source playback URLs once via the local API")
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError(args.out)
    if args.input.stat().st_size > 32 * 1024 * 1024:
        parser.error("Blind input exceeds 32 MiB")
    html = render_review(json.loads(args.input.read_text(encoding="utf-8")), args.api_base, resolve_playback=args.resolve_playback)
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(html)
    print(str(args.out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
