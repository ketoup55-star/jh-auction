/* 관심물건 등록 모달 (공용) — auction.html(상세)·auctions.html(목록)·mypage.html 공용.
   호출: window.openFavModal(keys, label, onSaved)
     keys    = item_key(문자열) 또는 배열(목록 다중 선택)
     label   = 헤더에 표시할 사건 라벨
     onSaved = 저장/해제 후 콜백(keys). 없으면 기본 알림.
   기본폴더 12개 고정 + 개인폴더(/folders) + 개인폴더관리 패널(추가/이름변경/▲▼순서/삭제). */
(function(){
  const BASE=["기타","실거주","장기보유투자","단기매각투자","임대수익투자","재개발","재건축","뉴타운","공동투자","컨설팅물건","학습용","낙찰"];
  const S={keys:[],label:"",folder:"기타",importance:0,memo:"",notify:1,personal:[],onSaved:null,existing:false};
  let root=null, mgr=null;

  function injectCss(){
    if(document.getElementById('favm-css')) return;
    const st=document.createElement('style'); st.id='favm-css';
    st.textContent=`
    .favm-ov{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:99999;display:none;align-items:flex-start;justify-content:center;overflow:auto;padding:24px 12px;}
    .favm-ov.on{display:flex;}
    .favm{background:#fff;width:560px;max-width:96vw;border-radius:10px;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.3);font-family:'Malgun Gothic','맑은 고딕',system-ui,sans-serif;color:#222;}
    .favm-hd{background:#1f4fa3;color:#fff;padding:12px 18px;font-size:16px;font-weight:700;display:flex;justify-content:space-between;align-items:center;}
    .favm-hd .sa{font-size:11px;opacity:.55;font-weight:600;letter-spacing:1px;}
    .favm-x{background:none;border:none;color:#fff;font-size:19px;cursor:pointer;line-height:1;padding:0 2px;}
    .favm-case{padding:13px 18px;font-size:15px;font-weight:700;color:#1f4fa3;border-bottom:1px solid #eee;}
    .favm-sec{padding:12px 18px;border-bottom:1px solid #f1f1f1;}
    .favm-sec h4{margin:0 0 9px;font-size:13.5px;color:#333;display:flex;align-items:center;}
    .favm-hint{font-size:11px;color:#999;font-weight:400;margin-left:6px;}
    .favm-folders{display:flex;gap:10px;}
    .favm-col{flex:1;border:1px solid #d6dbe5;border-radius:6px;overflow:hidden;display:flex;flex-direction:column;}
    .favm-colhd{background:#dce6f7;color:#23457e;font-weight:700;font-size:13px;padding:7px 10px;display:flex;justify-content:space-between;align-items:center;}
    .favm-mng{background:#2c5db0;color:#fff;border:none;border-radius:5px;font-size:12px;padding:4px 10px;cursor:pointer;}
    .favm-list{max-height:158px;overflow:auto;}
    .favm-f{padding:7px 10px;font-size:13px;cursor:pointer;border-top:1px solid #f0f0f0;}
    .favm-f:first-child{border-top:none;}
    .favm-f:hover{background:#f3f7fd;}
    .favm-f.sel{background:#1f4fa3;color:#fff;}
    .favm-empty{padding:12px 10px;color:#aaa;font-size:12px;}
    .favm-stars{font-size:30px;color:#dcdcdc;cursor:pointer;user-select:none;letter-spacing:4px;}
    .favm-stars span:hover,.favm-stars span.on{color:#f5b301;}
    .favm-reset{background:#2c5db0;color:#fff;border:none;border-radius:5px;font-size:11px;padding:3px 9px;cursor:pointer;margin-left:auto;}
    .favm textarea{width:100%;height:88px;border:1px solid #cdd5e2;border-radius:6px;padding:9px;font-size:13px;resize:vertical;box-sizing:border-box;font-family:inherit;}
    .favm-notify label{margin-right:20px;font-size:14px;cursor:pointer;}
    .favm-ft{padding:14px;text-align:center;}
    .favm-save{background:#1f4fa3;color:#fff;border:none;border-radius:6px;padding:10px 44px;font-size:15px;font-weight:700;cursor:pointer;}
    .favm-save:disabled{opacity:.6;cursor:default;}
    .favm-rm{background:#fff;color:#c0392b;border:1px solid #e0b4ae;border-radius:6px;padding:10px 20px;font-size:14px;cursor:pointer;margin-right:10px;}
    .favm-addrow{display:flex;gap:6px;align-items:center;}
    .favm-addrow input{flex:1;border:1px solid #c9d2e0;border-radius:5px;padding:7px 9px;font-size:13px;}
    .favm-mgrlist{max-height:52vh;overflow:auto;}
    .favm-mgrrow{display:flex;align-items:center;gap:5px;padding:7px 14px;border-bottom:1px solid #f2f2f2;}
    .favm-fi{font-size:16px;}
    .favm-arr{background:#eef2f8;border:1px solid #d2dae8;border-radius:4px;color:#3a567f;cursor:pointer;font-size:11px;padding:3px 6px;line-height:1;}
    .favm-arr:disabled{opacity:.35;cursor:default;}
    .favm-mgrname{flex:1;border:1px solid #cfd6e2;border-radius:5px;padding:5px 8px;font-size:13px;min-width:0;}
    `;
    document.head.appendChild(st);
  }

  function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
  function enc(s){return encodeURIComponent(s);}
  function dec(s){return decodeURIComponent(s);}

  async function api(path, method, body){          // 공통 fetch(401 리다이렉트 + 에러 알림 + JSON)
    try{ const r=await fetch(path,{method:method||'GET',
        headers:body?{'Content-Type':'application/json'}:undefined, body:body?JSON.stringify(body):undefined});
      const d=await r.json().catch(()=>({}));
      if(r.status===401){ alert('로그인이 필요합니다.'); location.href='/static/login.html?next='+encodeURIComponent(location.pathname+location.search); return null; }
      if(!r.ok){ alert(d.detail||'처리 실패'); return null; }
      return d;
    }catch(e){ alert('처리 실패'); return null; }
  }

  function build(){
    injectCss();
    root=document.createElement('div'); root.className='favm-ov';
    root.innerHTML=`
    <div class="favm" role="dialog" aria-label="관심물건등록">
      <div class="favm-hd"><span>관심물건등록</span><span style="display:flex;align-items:center;gap:12px"><span class="sa">JH옥션스쿨</span><button class="favm-x" data-close>✕</button></span></div>
      <div class="favm-case" id="favm-case"></div>
      <div class="favm-sec">
        <div class="favm-folders">
          <div class="favm-col"><div class="favm-colhd">기본폴더</div><div class="favm-list" id="favm-base"></div></div>
          <div class="favm-col"><div class="favm-colhd">개인폴더 <button class="favm-mng" id="favm-mng">폴더관리</button></div>
            <div class="favm-list" id="favm-personal"></div></div>
        </div>
      </div>
      <div class="favm-sec"><h4>중요도 <span class="favm-hint">(별점을 선택해주세요)</span><button class="favm-reset" id="favm-starreset">초기화</button></h4>
        <div class="favm-stars" id="favm-stars"></div></div>
      <div class="favm-sec"><h4>메모</h4><textarea id="favm-memo" placeholder="메모 입력"></textarea></div>
      <div class="favm-sec favm-notify"><h4>알림/입찰달력 표시 여부 <span class="favm-hint">(제외 시 입찰달력 및 관심물건 변경 알림에서 제외됨)</span></h4>
        <label><input type="radio" name="favm-notify" value="1" checked> 표시</label>
        <label><input type="radio" name="favm-notify" value="0"> 제외</label></div>
      <div class="favm-ft"><button class="favm-rm" id="favm-rm" style="display:none">관심물건 해제</button><button class="favm-save" id="favm-save">저장</button></div>
    </div>`;
    document.body.appendChild(root);
    root.addEventListener('click',e=>{ if(e.target===root||e.target.hasAttribute('data-close')) close(); });
    root.querySelector('#favm-mng').onclick=openMgr;
    root.querySelector('#favm-starreset').onclick=()=>{ S.importance=0; renderStars(); };
    root.querySelector('#favm-save').onclick=save;
    root.querySelector('#favm-rm').onclick=remove;
    root.querySelectorAll('input[name=favm-notify]').forEach(r=>r.onchange=()=>{ const c=root.querySelector('input[name=favm-notify]:checked'); S.notify=c?+c.value:1; });
    document.addEventListener('keydown',e=>{ if(e.key==='Escape'){ if(mgr&&mgr.classList.contains('on')) closeMgr(); else if(root.classList.contains('on')) close(); } });
  }

  function renderFolders(){
    const base=root.querySelector('#favm-base'), per=root.querySelector('#favm-personal');
    base.innerHTML=BASE.map(f=>`<div class="favm-f${S.folder===f?' sel':''}" data-f="${enc(f)}">${esc(f)}</div>`).join('');
    per.innerHTML = S.personal.length
      ? S.personal.map(f=>`<div class="favm-f${S.folder===f?' sel':''}" data-f="${enc(f)}">${esc(f)}</div>`).join('')
      : `<div class="favm-empty">개인폴더 없음 — '폴더관리'로 추가</div>`;
    root.querySelectorAll('.favm-f').forEach(el=>el.onclick=()=>{ S.folder=dec(el.getAttribute('data-f')); renderFolders(); });
  }

  function renderStars(){
    const s=root.querySelector('#favm-stars');
    s.innerHTML=[1,2,3,4,5].map(i=>`<span data-s="${i}" class="${i<=S.importance?'on':''}">★</span>`).join('');
    s.querySelectorAll('span').forEach(sp=>sp.onclick=()=>{ const v=+sp.getAttribute('data-s'); S.importance=(S.importance===v?v-1:v); renderStars(); });
  }

  async function loadPersonal(){ const d=await api('/folders'); S.personal=(d&&d.folders)||[]; }

  // ── 개인폴더관리 패널 (메인 모달 위 오버레이) ──
  function buildMgr(){
    mgr=document.createElement('div'); mgr.className='favm-ov'; mgr.style.zIndex='100000';
    mgr.innerHTML=`
    <div class="favm" style="width:520px" role="dialog" aria-label="개인폴더관리">
      <div class="favm-hd"><span>개인폴더관리</span><span style="display:flex;align-items:center;gap:12px"><span class="sa">JH옥션스쿨</span><button class="favm-x" data-mclose>✕</button></span></div>
      <div class="favm-sec"><div class="favm-addrow">
        <input id="favm-mgr-new" maxlength="30" placeholder="폴더명 입력">
        <button class="favm-mng" id="favm-mgr-add" style="padding:7px 14px;font-size:13px">폴더추가</button>
      </div></div>
      <div class="favm-mgrlist" id="favm-mgrlist"></div>
    </div>`;
    document.body.appendChild(mgr);
    mgr.addEventListener('click',e=>{ if(e.target===mgr||e.target.hasAttribute('data-mclose')) closeMgr(); });
    mgr.querySelector('#favm-mgr-add').onclick=mgrAdd;
    mgr.querySelector('#favm-mgr-new').addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault();mgrAdd();} });
  }

  function renderMgr(){
    const box=mgr.querySelector('#favm-mgrlist');
    box.innerHTML = S.personal.length ? S.personal.map((f,i)=>`
      <div class="favm-mgrrow">
        <span class="favm-fi">📁</span>
        <button class="favm-arr" data-up="${i}" ${i===0?'disabled':''}>▲</button>
        <button class="favm-arr" data-dn="${i}" ${i===S.personal.length-1?'disabled':''}>▼</button>
        <input class="favm-mgrname" value="${esc(f)}" maxlength="30">
        <button class="favm-mng favm-edit" data-old="${enc(f)}">수정</button>
        <button class="favm-mng favm-mdel" data-del="${enc(f)}" style="background:#c0392b">삭제</button>
      </div>`).join('') : `<div class="favm-empty">개인폴더가 없습니다. 위에서 추가하세요.</div>`;
    box.querySelectorAll('[data-up]').forEach(b=>b.onclick=()=>moveFolder(+b.getAttribute('data-up'),-1));
    box.querySelectorAll('[data-dn]').forEach(b=>b.onclick=()=>moveFolder(+b.getAttribute('data-dn'),1));
    box.querySelectorAll('.favm-edit').forEach(b=>b.onclick=()=>{ const row=b.closest('.favm-mgrrow');
      renameFolder(dec(b.getAttribute('data-old')), row.querySelector('.favm-mgrname').value); });
    box.querySelectorAll('.favm-mdel').forEach(b=>b.onclick=()=>delFolder(dec(b.getAttribute('data-del'))));
  }

  let _foldersCb=null;
  function openMgr(){ if(!mgr) buildMgr(); mgr.querySelector('#favm-mgr-new').value=''; renderMgr(); mgr.classList.add('on'); }
  function closeMgr(){ if(mgr) mgr.classList.remove('on'); if(_foldersCb){ try{ _foldersCb(S.personal); }catch(e){} _foldersCb=null; } }
  // 마이페이지 등에서 단독으로 개인폴더관리 패널 열기(개인폴더 로드 후) + 변경분 콜백
  window.openFolderManager=async function(onChange){ injectCss(); _foldersCb=onChange||null; try{ await loadPersonal(); }catch(e){} openMgr(); };

  async function mgrAdd(){
    const inp=mgr.querySelector('#favm-mgr-new'); const name=(inp.value||'').trim(); if(!name) return;
    const d=await api('/folders','POST',{name}); if(d){ S.personal=d.folders||[]; inp.value=''; renderMgr(); renderFolders(); }
  }
  async function renameFolder(old,nw){
    nw=(nw||'').trim(); if(!nw||nw===old){ renderMgr(); return; }
    const d=await api('/folders/rename','POST',{old,new:nw}); if(d){ S.personal=d.folders||[]; if(S.folder===old) S.folder=nw; renderMgr(); renderFolders(); }
  }
  async function moveFolder(i,dir){
    const j=i+dir; if(j<0||j>=S.personal.length) return;
    const a=S.personal.slice(); const t=a[i]; a[i]=a[j]; a[j]=t;
    const d=await api('/folders/reorder','POST',{names:a}); if(d){ S.personal=d.folders||a; renderMgr(); renderFolders(); }
  }
  async function delFolder(name){
    if(!confirm(`'${name}' 폴더를 삭제할까요?\n(이 폴더의 관심물건은 '기타'로 이동됩니다)`)) return;
    const d=await api('/folders/'+encodeURIComponent(name),'DELETE'); if(d){ S.personal=d.folders||[]; if(S.folder===name) S.folder='기타'; renderMgr(); renderFolders(); }
  }

  async function save(){
    S.memo=root.querySelector('#favm-memo').value;
    const body=JSON.stringify({folder:S.folder,importance:S.importance,memo:S.memo,notify:S.notify});
    const btn=root.querySelector('#favm-save'); btn.disabled=true; const t0=btn.textContent; btn.textContent='저장 중…';
    try{
      let unauth=false;
      for(const k of S.keys){ const r=await fetch('/favorites/'+encodeURIComponent(k),{method:'POST',headers:{'Content-Type':'application/json'},body}); if(r.status===401){ unauth=true; break; } }
      if(unauth){ alert('로그인이 필요합니다.'); location.href='/static/login.html?next='+encodeURIComponent(location.pathname+location.search); return; }
      close();
      if(typeof S.onSaved==='function') S.onSaved(S.keys.slice());
      else alert((S.keys.length>1?S.keys.length+'건이 ':'')+'관심물건에 저장되었습니다.');
    }catch(e){ alert('저장 실패'); }
    finally{ btn.disabled=false; btn.textContent=t0; }
  }

  async function remove(){
    if(!confirm('이 물건을 관심물건에서 해제할까요?')) return;
    const btn=root.querySelector('#favm-rm'); btn.disabled=true;
    try{ for(const k of S.keys){ await fetch('/favorites/'+encodeURIComponent(k),{method:'DELETE'}); }
      close(); if(typeof S.onSaved==='function') S.onSaved(S.keys.slice()); else alert('관심물건에서 해제되었습니다.');
    }catch(e){ alert('해제 실패'); } finally{ btn.disabled=false; }
  }

  function close(){ if(root) root.classList.remove('on'); }

  window.openFavModal=async function(keys, label, onSaved){
    keys=Array.isArray(keys)?keys.filter(Boolean):(keys?[keys]:[]);
    if(!keys.length){ alert('물건을 선택하세요.'); return; }
    try{ const me=await fetch('/auth/me'); if(me.status===401){ alert('로그인이 필요합니다.'); location.href='/static/login.html?next='+encodeURIComponent(location.pathname+location.search); return; } }catch(e){}
    if(!root) build();
    S.keys=keys; S.label=label||''; S.onSaved=onSaved||null;
    S.folder='기타'; S.importance=0; S.memo=''; S.notify=1; S.existing=false;
    root.querySelector('#favm-case').textContent = label || (keys.length>1?`선택한 ${keys.length}건`:keys[0]);
    await loadPersonal();
    if(keys.length===1){
      try{ const st=await (await fetch('/favorites/'+encodeURIComponent(keys[0])+'/status')).json();
        if(st.favorite && st.meta){ S.folder=st.meta.folder||'기타'; S.importance=st.meta.importance||0;
          S.memo=st.meta.memo||''; S.notify=(st.meta.notify==null?1:(st.meta.notify?1:0)); S.existing=true; }
      }catch(e){}
    }
    root.querySelector('#favm-memo').value=S.memo;
    root.querySelector('#favm-save').textContent=S.existing?'수정 저장':'저장';
    root.querySelector('#favm-rm').style.display=(keys.length===1&&S.existing)?'inline-block':'none';
    const nr=root.querySelector(`input[name=favm-notify][value="${S.notify}"]`); if(nr) nr.checked=true;
    renderFolders(); renderStars();
    root.classList.add('on');
  };
})();
