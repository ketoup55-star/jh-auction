/* 회원 맞춤 대출·세금 분석 — 경매·공매 상세의 'AI 권리분석' 카드에서 재사용.
 * personalLoanAnalysis({purpose, houseCount, reg, usage}) → HTML 문자열(줄 띄움).
 *  - purpose: ['live','invest'] 중 복수(실거주/투자)
 *  - houseCount: 'none'|'one'|'multi' (무주택/1주택/다주택)
 *  - reg: 'regulated'|'metro'|'none' (규제지역·토허 / 수도권 비규제 / 지방 비규제)
 *  - usage: 물건 용도(주택류만 분석)
 * ⚠️ 매수 양호·검토·금지 램프(buy_grade)와는 완전 별개 — 그 램프는 절대 건드리지 않음.
 * 자체 CSS를 1회 주입하므로 어떤 페이지든 <script src> 만으로 동작(공매 재사용 대비).
 */
(function(){
  // (reg × houseCount) 대출·세금 사실 — LOAN_MATRIX와 동일 근거(2026.7.1 규제 기준).
  var FACTS = {
    regulated: {
      none:  {ga:'ok',   gaDetail:'LTV 40% (한도 15억↓ 6억·15~25억 4억·25억↑ 2억)', jeonip:true,  cheobun:false, biz:false, tax:'base'},
      one:   {ga:'cond', gaDetail:'처분조건부 — 6개월 내 기존주택 처분 시 LTV 40%, 미처분 시 0%', jeonip:true, cheobun:true, biz:false, tax:'heavy'},
      multi: {ga:'no',   gaDetail:'추가 주택구입 주담대 금지 (LTV 0%)', jeonip:false, cheobun:false, biz:false, tax:'heavy'}
    },
    metro: {
      none:  {ga:'ok',   gaDetail:'LTV 70% (한도 6/4/2억, 수도권 차등)', jeonip:false, cheobun:false, biz:false, tax:'base'},
      one:   {ga:'cond', gaDetail:'처분조건부 — 6개월 내 처분 시 LTV 70%, 미처분 시 0%', jeonip:false, cheobun:true, biz:false, tax:'base'},
      multi: {ga:'no',   gaDetail:'추가 주택구입 주담대 금지 (LTV 0%)', jeonip:false, cheobun:false, biz:false, tax:'base'}
    },
    none: {
      none:  {ga:'ok', gaDetail:'LTV 70% (스트레스금리 낮음·전입의무 없음)', jeonip:false, cheobun:false, biz:true, tax:'base'},
      one:   {ga:'ok', gaDetail:'처분조건 강제 없이 LTV 70%', jeonip:false, cheobun:false, biz:true, tax:'base'},
      multi: {ga:'ok', gaDetail:'2주택↑ LTV≈60% (DSR·방공제 유의)', jeonip:false, cheobun:false, biz:true, tax:'base'}
    }
  };
  var HC = {none:'무주택자', one:'1주택자', multi:'다주택자'};
  var REG = {regulated:'규제지역·토지거래허가구역', metro:'수도권 비규제', none:'지방 비규제'};
  var VCLR = {ok:'#1a7d46', cond:'#e08a1e', no:'#c0392b'};
  var VTXT = {ok:'대출 가능', cond:'검토 필요', no:'대출 불가'};

  function esc(s){ return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

  // 실거주 관점
  function liveSection(F, hc, reg){
    var p = [];
    var head = (F.ga==='ok') ? '가계 경락잔금대출이 <b>가능</b>합니다'
             : (F.ga==='cond') ? '<b>검토가 필요</b>합니다'
             : '가계 경락잔금대출은 <b>불가</b>합니다';
    p.push('<div class="pl-verdict" style="color:'+VCLR[F.ga]+'">실거주 목적이고 '+HC[hc]+'이기 때문에 '+head+'.</div>');

    if(F.ga==='no'){
      p.push('<p>다주택 규제로 <b>가계 주담대가 막혀</b> 있어, 이 지역 주택을 실거주로 추가 취득하려면 자기자본 비중이 커집니다.</p>');
    } else {
      var cond = '<p><b>가계대출</b> — '+F.gaDetail+'.';
      if(F.jeonip) cond += ' 실행일부터 <b>6개월 내 전입의무</b>가 붙습니다(실거주 목적엔 자연스러운 조건).';
      cond += '</p>';
      p.push(cond);
    }
    // 실거주 특화 — 가계가 걸리면(조건부·불가) 대안의 부적합성 안내
    if(F.ga!=='ok'){
      p.push('<p>기존주택 처분이나 전입을 원치 않으면 <b>사업자대출·P2P</b>가 대안이지만 — '
        + '<b>P2P는 고금리</b>라 실거주에 부적합하고, <b>사업자(신탁)대출은 사업 목적이 아닌 실거주로 쓰는 것이라 대출 용도와 맞지 않아 리스크</b>가 있습니다.</p>');
    }
    if(F.tax==='heavy') p.push('<p class="pl-sub">매도 시 다주택 <b>비교과세(중과)</b> 대상이 될 수 있어 실거주라도 처분 시점 세금을 함께 보세요.</p>');
    return p.join('');
  }

  // 투자 관점
  function investSection(F, hc, reg){
    var p = [];
    var head = (F.ga==='ok') ? '<b>대출은 가능</b>합니다'
             : (F.ga==='cond') ? '<b>검토가 필요</b>합니다'
             : '가계 주담대는 <b>불가</b>합니다';
    p.push('<div class="pl-verdict" style="color:'+VCLR[F.ga]+'">투자 목적이고 '+HC[hc]+'이기 때문에 '+head+'.</div>');

    if(F.ga!=='no'){
      var cond = '<p><b>가계대출</b> — '+F.gaDetail+'.';
      if(F.jeonip) cond += ' 단 <b>6개월 내 전입이 필요</b>해, 투자라도 본인이 들어가 살아야 하므로 즉시 임대는 어렵습니다.';
      cond += '</p>';
      p.push(cond);
    }
    // 사업자대출(투자 레버리지)
    if(F.biz){
      p.push('<p><b>매매·임대 사업자대출도 가능</b>한 구간이라 투자 레버리지가 열려 있습니다'
        + (hc==='multi' ? ' — <b>매매사업자 대출이 열리는 유일한 구간</b>입니다.' : '.') + '</p>');
    } else {
      p.push('<p>사업자대출은 <b>'+REG[reg]+' 규제로 LTV 0%</b>라 투자 레버리지가 막혀 있습니다. 신탁·전자상거래 사업자대출 등 <b>단기매도 우회로는 리스크</b>가 큽니다.</p>');
    }
    if(F.tax==='heavy'){
      p.push('<p class="pl-sub">매도 세금 — 다주택 <b>비교과세(중과)</b>로 매매업 실익이 줄어듭니다.</p>');
    } else {
      p.push('<p class="pl-sub">매도 세금 — 비조정지역이라 <b>중과 없이 기본세율</b>(6~45%).</p>');
    }
    return p.join('');
  }

  window.personalLoanAnalysis = function(opts){
    opts = opts || {};
    var purpose = opts.purpose || [], houseCount = opts.houseCount || '', reg = opts.reg || '', usage = opts.usage || '';
    var isHouse = /아파트|다세대|연립|빌라|도시형|다가구|단독|주택|농가/.test(usage) && !/오피스텔|상가|근린상가|토지/.test(usage);
    if(!isHouse) return '';                       // 주택류만
    if(!reg || !FACTS[reg]) return '';            // 규제상태 불명이면 표시 안 함
    var hasP = purpose && purpose.length, hcOK = FACTS[reg][houseCount];
    if(!hasP || !hcOK){
      // 회원이 목적·주택수를 아직 설정 안 함 → 설정 유도
      return '<div class="pl-box"><div class="pl-hd">🧭 내 조건 맞춤 분석</div>'
        + '<p class="pl-setup">회원정보에 <b>목적(실거주/투자)</b>과 <b>보유 주택 수</b>를 설정하면, 이 물건을 <b>내 상황에서 매수 가능한지</b> 맞춤 분석해 드립니다.<br><a href="/static/mypage_profile.html">마이페이지 → 회원정보수정에서 설정하기</a></p></div>';
    }
    var F = FACTS[reg][houseCount];
    var tags = [];
    if(purpose.indexOf('live')>=0) tags.push('실거주');
    if(purpose.indexOf('invest')>=0) tags.push('투자');
    tags.push(HC[houseCount]); tags.push(REG[reg]);

    var secs = [];
    if(purpose.indexOf('live')>=0)   secs.push('<div class="pl-sec">'+liveSection(F, houseCount, reg)+'</div>');
    if(purpose.indexOf('invest')>=0) secs.push('<div class="pl-sec">'+investSection(F, houseCount, reg)+'</div>');

    return '<div class="pl-box">'
      + '<div class="pl-hd">🧭 내 조건 맞춤 분석 <span class="pl-tag">'+esc(tags.join(' · '))+'</span></div>'
      + secs.join('<div class="pl-div"></div>')
      + '<div class="pl-foot">※ 회원님이 설정한 목적·보유주택수 기준 안내입니다. 실제 대출·세무는 조건·물건별로 달라질 수 있어 전문가 확인을 권합니다.</div>'
      + '</div>';
  };

  // 자체 CSS 1회 주입 (공매 등 어느 페이지에서 include 해도 동작)
  if(!document.getElementById('pl-style')){
    var st = document.createElement('style'); st.id = 'pl-style';
    st.textContent =
      '.pl-box{margin-top:11px;padding:12px 14px;border-radius:8px;background:#f4f8ff;border:1px solid #cfe0f5;}'
      +'.pl-hd{font-weight:800;color:#1b4f8a;font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;}'
      +'.pl-tag{font-weight:700;font-size:11.5px;color:#2c6bbf;background:#e2edff;border:1px solid #c2d8f5;border-radius:20px;padding:2px 10px;}'
      +'.pl-sec{font-size:12.7px;line-height:1.7;color:#2f3a48;}'
      +'.pl-verdict{font-weight:800;font-size:13.5px;margin:2px 0 6px;}'
      +'.pl-sec p{margin:6px 0;word-break:keep-all;}'
      +'.pl-sec .pl-sub{color:#5a6472;font-size:12px;}'
      +'.pl-div{height:1px;background:#d3e0f2;margin:12px 0;}'
      +'.pl-setup{font-size:12.5px;line-height:1.7;color:#33404f;}'
      +'.pl-setup a{color:#1565c0;font-weight:700;}'
      +'.pl-foot{margin-top:10px;font-size:11px;color:#8a97a6;line-height:1.5;}';
    document.head.appendChild(st);
  }
})();
