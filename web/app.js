'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="csrf-token"]').content;
let state = null;
let serverOffset = 0;
let checks = [];
let lastPhase = null;
let initialized = false;
let toastTimer;
let polling = false;
const phaseNames = {setup:'等待开始',running:'正在作答',collecting:'正在回收',collected:'回收完成',error:'回收异常'};
const localTime = (ts) => new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(ts*1000));

function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => {$('toast').hidden=true;}, 5000);
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败');
  return result;
}
function candidateData() {return {candidate:$('candidate').value.trim(),workspace:$('workspace').value.trim()};}
function updatePath() {
  const {candidate,workspace}=candidateData();
  $('candidate-path').textContent = `${workspace || '工作文件夹'} / ${candidate || '准考证号'} / 题目英文名 / 题目英文名.cpp`;
}
function renderProblems() {
  const problems = state.settings?.problems || Array.from({length:4},(_,i)=>({title:`第 ${i+1} 题待设置`,slug:''}));
  $('problems').replaceChildren();
  problems.forEach((problem,i)=>{
    const result = state.phase === 'collected' ? state.result.problems[i] : checks[i];
    const card = document.createElement('article'); card.className='problem-card';
    const top = document.createElement('div'); top.className='problem-card-top';
    const number = document.createElement('span'); number.className='problem-number'; number.textContent=`P${String(i+1).padStart(2,'0')}`;
    const status = document.createElement('span'); status.className='problem-status';
    status.textContent = result ? (result.status==='collected' ? (state.phase==='collected'?'已回收':'已找到') : (state.phase==='collected'?'未回收':'待检查')) : (problem.slug?'等待检查':'尚未配置');
    if (result) status.classList.add(result.status==='collected'?'ok':'bad');
    top.append(number,status);
    const name = document.createElement('h3'); name.textContent=problem.title;
    const slug = document.createElement('div'); slug.className='slug'; slug.textContent=problem.slug || '在 Settings 中填写题目信息';
    const path = document.createElement('div'); path.className='path'; path.textContent=problem.slug?`${problem.slug} / ${problem.slug}.cpp`:'— / —.cpp';
    card.append(top,name,slug,path);
    if(result){const detail=document.createElement('div'); detail.className='detail';detail.textContent=result.status==='collected'?`${result.bytes} 字节${result.bytes===0?' · 空文件':''}`:result.message;card.append(detail);}
    $('problems').append(card);
  });
}
function applyState(next) {
  state=next; serverOffset=next.now*1000-Date.now();
  const locked=state.phase!=='setup', done=['collected','error'].includes(state.phase);
  $('candidate').disabled=locked; $('workspace').disabled=locked; $('browse-button').disabled=locked;
  $('settings-button').disabled=locked;
  if (locked) {$('candidate').value=state.candidate; $('workspace').value=state.workspace;}
  $('phase-badge').textContent=phaseNames[state.phase];
  $('start-button').hidden=locked; $('start-button').disabled=!state.settings;
  $('check-button').hidden=done || state.phase==='collecting'; $('check-button').disabled=!state.settings;
  $('start-help').textContent=done?'本场已结束。请核对回收结果；后续修改不会计入本场答卷。':locked?'本场信息已锁定。请在编辑器中作答并及时保存文件。':(state.settings?'设置已保存。确认回收时间和工作文件夹后开始模拟。':'开始前，请先完成右上角的必填 Settings。');
  $('settings-dot').hidden=Boolean(state.settings);
  $('completion-actions').hidden=!done; $('report-link').hidden=state.phase!=='collected';
  $('retry-button').hidden=state.phase!=='error';
  $('error-panel').hidden=state.phase!=='error'; $('error-panel').textContent=state.error || '';
  $('deadline-display').textContent=state.settings?localTime(state.settings.deadline_ts):'请先设置回收时间';
  const step=state.phase==='setup'?1:done?3:2;
  [1,2,3].forEach(i=>$(`step-${i}`).classList.toggle('current',i===step));
  if(state.phase==='collected'){
    $('timer-note').textContent=`已回收 ${state.result.problems.filter(p=>p.status==='collected').length} / 4 个源程序，请打开报告核对。`;
    $('check-time').textContent='已封存 · '+localTime(state.result.collected_at);
  } else {
    $('timer-note').textContent='到点自动封存四题源程序，并打开可复制代码的 HTML 报告。';
    if(!checks.length) $('check-time').textContent=state.settings?'以实际回收结果为准':'等待配置题目';
  }
  if(lastPhase && lastPhase!=='collected' && state.phase==='collected') toast('源程序回收完成，HTML 报告已保存。');
  if(locked && $('settings-dialog').open) $('settings-dialog').close();
  lastPhase=state.phase;
  updatePath(); renderProblems(); tick();
}
function tick(){
  if(!state)return;
  $('timer-indicator').textContent=phaseNames[state.phase];
  $('timer-heading').textContent=state.phase==='collected'?'本场模拟已结束':state.phase==='error'?'请检查回收异常':'距离自动回收';
  if(!state.settings || state.phase==='setup'){$('countdown').textContent='--:--:--';return;}
  const left=['collected','error'].includes(state.phase)?0:Math.max(0,Math.ceil((state.settings.deadline_ts*1000-Date.now()-serverOffset)/1000));
  const hours=String(Math.floor(left/3600)).padStart(2,'0'), minutes=String(Math.floor(left/60)%60).padStart(2,'0'), seconds=String(left%60).padStart(2,'0');
  $('countdown').textContent=`${hours}:${minutes}:${seconds}`;
}
function openSettings(){
  if(state?.phase!=='setup')return;
  const settings=state.settings;
  $('deadline').value=settings?localTime(settings.deadline_ts).replace(' ','T'):'';
  $('settings-error').textContent='';
  for(let i=0;i<4;i++){$(`title-${i}`).value=settings?.problems[i].title || '';$(`slug-${i}`).value=settings?.problems[i].slug || '';}
  $('settings-dialog').showModal();
}
for(let i=0;i<4;i++){
  const row=document.createElement('div');row.className='settings-row';
  const number=document.createElement('span');number.textContent=String(i+1).padStart(2,'0');
  const title=document.createElement('input');title.id=`title-${i}`;title.required=true;title.maxLength=100;title.placeholder=`第 ${i+1} 题名称`;title.setAttribute('aria-label',`第 ${i+1} 题名称`);
  const slug=document.createElement('input');slug.id=`slug-${i}`;slug.required=true;slug.maxLength=50;slug.pattern='[a-z][a-z0-9_]*';slug.placeholder=['airplane','cashier','dune','manhattan'][i];slug.spellcheck=false;slug.setAttribute('aria-label',`第 ${i+1} 题英文名`);
  row.append(number,title,slug);$('problem-settings').append(row);
}
$('candidate').addEventListener('input',updatePath);$('workspace').addEventListener('input',updatePath);
$('settings-button').addEventListener('click',openSettings);
$('close-settings').addEventListener('click',()=>$('settings-dialog').close());
$('settings-form').addEventListener('submit',async e=>{
  e.preventDefault();const button=e.submitter;button.disabled=true;
  try{
    const result=await api('/api/settings',{deadline:$('deadline').value+'+08:00',problems:Array.from({length:4},(_,i)=>({title:$(`title-${i}`).value.trim(),slug:$(`slug-${i}`).value.trim()}))});
    checks=[];applyState(result);$('settings-dialog').close();toast('考前设置已保存');
  }catch(error){$('settings-error').textContent=error.message;}finally{button.disabled=false;}
});
let folderSelection=null;
let folderRequest=0;
async function loadFolders(path){
  const request=++folderRequest;
  $('choose-folder').disabled=true;$('folder-error').textContent='';$('folder-count').textContent='正在读取…';
  try{
    const result=await api('/api/folders',{path});if(request!==folderRequest)return;
    folderSelection=result;$('folder-address').value=result.path;
    $('folder-list').replaceChildren();$('folder-roots').replaceChildren();
    result.roots.forEach(root=>{const button=document.createElement('button');button.className='secondary';button.textContent=root.name;button.addEventListener('click',()=>loadFolders(root.path));$('folder-roots').append(button);});
    result.folders.forEach(folder=>{const button=document.createElement('button');button.className='folder-entry';const label=document.createElement('span');label.textContent=folder.name;const arrow=document.createElement('span');arrow.textContent='›';button.append(label,arrow);button.addEventListener('click',()=>loadFolders(folder.path));$('folder-list').append(button);});
    if(!result.folders.length){const empty=document.createElement('p');empty.className='field-hint';empty.textContent='当前目录没有子文件夹，可以直接选择。';$('folder-list').append(empty);}
    $('folder-count').textContent=`${result.folders.length} 个子文件夹${result.truncated?'（仅显示前 1000 个，可直接输入路径）':''}`;
    $('choose-folder').disabled=false;$('folder-up').disabled=result.parent===result.path;
  }catch(error){if(request!==folderRequest)return;folderSelection=null;$('folder-error').textContent=error.message;$('folder-count').textContent='无法读取目录';$('folder-list').replaceChildren();}
}
$('browse-button').addEventListener('click',()=>{$('folder-dialog').showModal();loadFolders($('workspace').value.trim());});
$('close-folder').addEventListener('click',()=>$('folder-dialog').close());
$('folder-address-form').addEventListener('submit',event=>{event.preventDefault();loadFolders($('folder-address').value.trim());});
$('folder-up').addEventListener('click',()=>{if(folderSelection)loadFolders(folderSelection.parent);});
$('choose-folder').addEventListener('click',()=>{if(folderSelection && state.phase==='setup'){$('workspace').value=folderSelection.path;updatePath();$('folder-dialog').close();}});
$('candidate-form').addEventListener('submit',async e=>{
  e.preventDefault();$('start-button').disabled=true;
  try{checks=[];applyState(await api('/api/start',candidateData()));toast('模拟已开始，请按规定建立目录并保存源程序。');}
  catch(error){toast(error.message);}finally{$('start-button').disabled=!state.settings;}
});
$('check-button').addEventListener('click',async()=>{
  $('check-button').disabled=true;
  try{const result=await api('/api/check',candidateData());checks=result.problems;renderProblems();$('check-time').textContent='最近检查 · '+localTime((Date.now()+serverOffset)/1000);toast('目录检查完成；检查结果不会代替截止时刻的回收。');}
  catch(error){toast(error.message);}finally{$('check-button').disabled=!state.settings;}
});
$('reset-button').addEventListener('click',()=>$('reset-dialog').showModal());
$('cancel-reset').addEventListener('click',()=>$('reset-dialog').close());
$('confirm-reset').addEventListener('click',async()=>{
  try{checks=[];applyState(await api('/api/reset',{}));$('reset-dialog').close();openSettings();}
  catch(error){toast(error.message);}
});
$('retry-button').addEventListener('click',async()=>{
  try{applyState(await api('/api/retry',{}));}catch(error){toast(error.message);}
});
async function poll(){
  if(polling)return;polling=true;
  try{applyState(await api('/api/state'));$('connection-error').hidden=true;
    if(!initialized){initialized=true;if(!state.settings)openSettings();}
  }catch(error){$('connection-error').hidden=false;}finally{polling=false;}
}
poll();setInterval(poll,1500);setInterval(tick,250);
