/* 로그인 직후 맞춤설정 모달 — 목적(실거주/투자, 복수)·보유 주택 수 미설정 회원에게만 1회 표시.
 * 한 번 저장하면 /auth/me 의 purpose·house_count 가 채워져 다시 뜨지 않음.
 * 수정은 마이페이지 → 회원정보수정. 어떤 페이지든 <script src="/static/profile_setup.js"> 만으로 동작(자체 주입).
 */
(function(){
  if(window.__psetupInit) return; window.__psetupInit = true;
  try{
    fetch('/auth/me').then(function(r){ return r.ok ? r.json() : null; }).then(function(u){
      if(!u) return;                                            // 비로그인
      if((u.purpose && u.purpose.length) && u.house_count) return;   // 이미 설정됨
      show();
    }).catch(function(){});
  }catch(e){}

  function show(){
    if(document.getElementById('psetup-ov')) return;
    var st = document.createElement('style'); st.id='psetup-style';
    st.textContent =
      '.psetup-ov{position:fixed;inset:0;background:rgba(20,28,40,.55);z-index:99999;display:flex;align-items:center;justify-content:center;padding:16px;}'
     +'.psetup-modal{background:#fff;border-radius:14px;max-width:440px;width:100%;padding:26px 24px 20px;box-shadow:0 18px 50px rgba(0,0,0,.3);font-family:inherit;}'
     +'.psetup-modal h2{margin:0 0 4px;font-size:19px;color:#1b4f8a;}'
     +'.psetup-modal .ps-sub{font-size:13px;color:#6b7684;margin:0 0 18px;line-height:1.55;}'
     +'.psetup-modal .ps-q{font-weight:800;font-size:14px;color:#25303f;margin:16px 0 8px;}'
     +'.psetup-modal .ps-q small{font-weight:400;color:#96a0ae;font-size:11.5px;margin-left:6px;}'
     +'.psetup-opts{display:flex;gap:9px;flex-wrap:wrap;}'
     +'.ps-opt{flex:1;min-width:96px;display:flex;align-items:center;justify-content:center;gap:7px;padding:12px 8px;border:1.5px solid #d7dfea;border-radius:10px;cursor:pointer;font-size:14px;font-weight:600;color:#3a4656;user-select:none;transition:.12s;}'
     +'.ps-opt:hover{border-color:#9db6dd;}'
     +'.ps-opt.on{border-color:#2c6bbf;background:#eef4ff;color:#1b4f8a;}'
     +'.ps-opt input{display:none;}'
     +'.psetup-msg{font-size:12.5px;color:#c0392b;min-height:16px;margin:12px 0 0;}'
     +'.psetup-actions{display:flex;align-items:center;gap:12px;margin-top:8px;}'
     +'.psetup-save{flex:1;height:44px;background:#2c6bbf;color:#fff;border:none;border-radius:9px;font-size:15px;font-weight:800;cursor:pointer;}'
     +'.psetup-save:disabled{opacity:.6;cursor:default;}'
     +'.psetup-later{color:#96a0ae;font-size:12.5px;cursor:pointer;text-decoration:underline;white-space:nowrap;}';
    document.head.appendChild(st);

    var ov = document.createElement('div'); ov.id='psetup-ov'; ov.className='psetup-ov';
    ov.innerHTML =
      '<div class="psetup-modal" role="dialog" aria-modal="true">'
      +'<h2>맞춤 분석 설정</h2>'
      +'<p class="ps-sub">물건 상세의 <b>AI 권리분석</b>에서 회원님 상황에 맞춰 대출 가능 여부를 안내해 드리기 위한 설정입니다. 한 번만 하면 됩니다.</p>'
      +'<div class="ps-q">투자 목적 <small>복수 선택 가능</small></div>'
      +'<div class="psetup-opts" id="ps-purpose">'
        +'<label class="ps-opt"><input type="checkbox" value="live">🏠 실거주</label>'
        +'<label class="ps-opt"><input type="checkbox" value="invest">📈 투자</label>'
      +'</div>'
      +'<div class="ps-q">보유 주택 수</div>'
      +'<div class="psetup-opts" id="ps-house">'
        +'<label class="ps-opt"><input type="radio" name="ps-hc" value="none">무주택자</label>'
        +'<label class="ps-opt"><input type="radio" name="ps-hc" value="one">1주택자</label>'
        +'<label class="ps-opt"><input type="radio" name="ps-hc" value="multi">다주택자</label>'
      +'</div>'
      +'<div class="psetup-msg" id="ps-msg"></div>'
      +'<div class="psetup-actions">'
        +'<button class="psetup-save" id="ps-save">저장하고 시작하기</button>'
        +'<span class="psetup-later" id="ps-later">나중에</span>'
      +'</div>'
      +'</div>';
    document.body.appendChild(ov);

    // 선택 토글(라벨 on 스타일)
    ov.querySelectorAll('.ps-opt').forEach(function(lab){
      var inp = lab.querySelector('input');
      inp.addEventListener('change', function(){
        if(inp.type==='radio'){
          lab.parentNode.querySelectorAll('.ps-opt').forEach(function(l){ l.classList.remove('on'); });
        }
        lab.classList.toggle('on', inp.checked);
      });
    });

    document.getElementById('ps-later').onclick = function(){ close(); };
    document.getElementById('ps-save').onclick = function(){
      var purpose = []; ov.querySelectorAll('#ps-purpose input:checked').forEach(function(i){ purpose.push(i.value); });
      var hcEl = ov.querySelector('#ps-house input:checked');
      var house_count = hcEl ? hcEl.value : '';
      var msg = document.getElementById('ps-msg');
      if(!purpose.length){ msg.textContent='목적을 1개 이상 선택하세요.'; return; }
      if(!house_count){ msg.textContent='보유 주택 수를 선택하세요.'; return; }
      var btn = document.getElementById('ps-save'); btn.disabled=true; btn.textContent='저장 중…'; msg.textContent='';
      fetch('/auth/prefs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose:purpose,house_count:house_count})})
        .then(function(r){ if(!r.ok) throw 0; return r.json(); })
        .then(function(){ close(); })
        .catch(function(){ btn.disabled=false; btn.textContent='저장하고 시작하기'; msg.textContent='저장에 실패했습니다. 잠시 후 다시 시도하세요.'; });
    };
  }

  function close(){
    var ov=document.getElementById('psetup-ov'); if(ov) ov.remove();
    var st=document.getElementById('psetup-style'); if(st) st.remove();
  }
})();
