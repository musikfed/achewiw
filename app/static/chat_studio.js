/* ACHEWIW 07/08: additive chat UI; existing manuscript and six panes untouched.
 * Owns only UI state + server-side chat records. No direct DOM HTML from Ollama.
 */
(()=>{
'use strict';
const workspaces=['generation','editorial'];
const state={
 generation:{projectId:null,threadId:null,busy:false,lastProposal:null,outline:null},
 editorial:{projectId:null,threadId:null,busy:false,head:null,lastProposal:null,pending:null}
};
const $=(root,selector)=>root.querySelector(selector);
const ui=kind=>document.querySelector('[data-chat-workspace="'+kind+'"]');
const projectId=()=>typeof project!=='undefined'&&project?.id||null;
const threadKey=(id,kind)=>'achewiw.chat.thread.v1.'+id+'.'+kind;
const storageGet=key=>{try{return localStorage.getItem(key)}catch{return null}};
const storageSet=(key,value)=>{try{localStorage.setItem(key,value)}catch{}};
const req=async(path,options={})=>{
 const res=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},
   cache:'no-store',credentials:'same-origin',...options});
 if(!res.ok){
  let message='HTTP '+res.status;
  try{const data=await res.json();message=String(data.detail||message)}catch{}
  throw Error(message);
 }
 return res.json();
};
const prefix=pid=>'/projects/'+encodeURIComponent(pid);
const post=(path,body)=>req(path,{method:'POST',body:JSON.stringify(body)});
const put=(path,body)=>req(path,{method:'PUT',body:JSON.stringify(body)});
const status=(kind,message)=>{const el=$(ui(kind),'[data-chat-status]');if(el)el.textContent=message};
const toggle=(kind,busy)=>{
 state[kind].busy=busy;
 const panel=ui(kind);
 panel.querySelectorAll('button,select,[data-chat-message]').forEach(el=>{
  if(el.matches('[data-chat-generate]')){el.disabled=busy||!state.editorial.lastProposal;return;}
  el.disabled=busy;
 });
};
const reset=kind=>{
 const st=state[kind],panel=ui(kind);
 st.threadId=null;st.projectId=projectId();st.busy=false;
 if(kind==='editorial'){st.head=null;st.lastProposal=null;st.pending=null;
   $(panel,'[data-chat-head]').textContent='Рабочая редакция не выбрана.';
   $(panel,'[data-chat-preview]').hidden=true;
   $(panel,'[data-chat-version]').replaceChildren();
   $(panel,'[data-chat-scene]').replaceChildren();}
 $(panel,'[data-chat-log]').replaceChildren();
 if(kind==='generation'){st.lastProposal=null;st.outline=null;
  $(panel,'[data-chat-outline-preview]').hidden=true;
  $(panel,'[data-chat-outline-status]').textContent='Пока нет предложенного каркаса.';
  $(panel,'[data-chat-book-status]').textContent='Книга не выбрана.';
  $(panel,'[data-chat-book-progress]').value=0;}
 $(panel,'[data-chat-message]').value='';
 status(kind,'Выберите работу в библиотеке и начните диалог.');
};
const entry=(log,role,text,subtitle)=>{
 const div=document.createElement('article');
 div.className='chat-entry '+(role==='author'?'chat-author':'chat-system');
 const title=document.createElement('strong');
 title.textContent=role==='author'?'Автор':'Помощник / план';
 div.append(title);
 if(subtitle){const small=document.createElement('small');small.textContent=subtitle;div.append(small);}
 const p=document.createElement('p');p.textContent=text;div.append(p);
 log.append(div);
 return div;
};
const renderConversation=(kind,data)=>{
 const panel=ui(kind),log=$(panel,'[data-chat-log]');
 log.replaceChildren();
 const proposals=new Map((data.proposals||[]).map(item=>[item.message_id,item]));
 if(!data.messages?.length)entry(log,'assistant',
  kind==='generation'?'Опишите замысел. Предложенные операции распределятся по существующим разделам 01–06.':
  'Создайте снимок рукописи, выберите главу, затем напишите, что нужно исправить.');
 for(const message of data.messages||[]){
  const proposal=proposals.get(message.id);
  entry(log,message.role,message.content,proposal?'Предложение: '+proposal.status:'');
  if(proposal){
   const summary=(proposal.plan?.actions||[]).map(a=>'• '+a.target+' — '+a.kind);
   if(proposal.plan?.question)summary.push('Уточнение: '+proposal.plan.question);
   if(proposal.plan?.warnings?.length)summary.push(...proposal.plan.warnings.map(x=>'⚠ '+x));
   if(summary.length)entry(log,'assistant',summary.join('\n'),'Маршрут команды · без автоматической записи');
  }
 }
 log.scrollTop=log.scrollHeight;
 if(kind==='generation'){
  state.generation.lastProposal=(data.proposals||[]).filter(p=>p.status==='proposed').at(-1)?.id||null;
 }
 if(kind==='editorial'){
  const p=(data.proposals||[]).filter(p=>p.scope==='chapter'&&p.status==='proposed').at(-1);
  state.editorial.lastProposal=p?.id||null;
  $(panel,'[data-chat-generate]').disabled=state.editorial.busy||!p;
 }
 $(panel,'[data-chat-mode]').value=data.thread.mode;
};
async function thread(kind,id){
 const st=state[kind],pid=projectId();
 if(!pid)return;
 if(st.projectId!==pid)reset(kind);
 if(id)st.threadId=id;
 if(!st.threadId)st.threadId=storageGet(threadKey(pid,kind));
 if(st.threadId){
  try{
   const data=await req(prefix(pid)+'/chats/'+encodeURIComponent(st.threadId));
   if(projectId()!==pid)return;
   if(data.thread.workspace!==kind)throw Error('Не совпадает мастерская диалога');
   renderConversation(kind,data);
   return;
  }catch(err){
   st.threadId=null;
   status(kind,'Создаём новый диалог: '+err.message);
  }
 }
 const data=await post(prefix(pid)+'/chats',{workspace:kind,
   mode:$(ui(kind),'[data-chat-mode]').value});
 if(projectId()!==pid)return;
 st.threadId=data.id;storageSet(threadKey(pid,kind),data.id);
 const full=await req(prefix(pid)+'/chats/'+data.id);
 renderConversation(kind,full);
}
async function versions(){
 const pid=projectId(),panel=ui('editorial');if(!pid)return;
 const selected=$(panel,'[data-chat-version]').value;
 const rows=await req(prefix(pid)+'/editorial/versions');
 if(projectId()!==pid)return;
 const select=$(panel,'[data-chat-version]');select.replaceChildren();
 for(const version of rows){
  const opt=document.createElement('option');opt.value=version.id;
  opt.textContent=(version.label||version.id)+' · '+(version.total_words||0)+' слов';
  select.append(opt);
 }
 if(rows.some(v=>v.id===selected))select.value=selected;
 if(!rows.length)status('editorial','Сохранённых редакций пока нет. Создайте снимок рукописи.');
}
async function head(){
 const pid=projectId();if(!pid)return;
 const panel=ui('editorial');
 try{
  const result=await req(prefix(pid)+'/chat-edition');
  if(projectId()!==pid)return;
  state.editorial.head=result;
  $(panel,'[data-chat-head]').textContent='Рабочая редакция: '+result.version_id.slice(0,12)+
    ' · '+result.chapters.length+' глав. Исходник остаётся неизменным.';
  const scene=$(panel,'[data-chat-scene]'),old=scene.value;scene.replaceChildren();
  for(const chapter of result.chapters){
   const option=document.createElement('option');option.value=chapter.scene_id;
   option.textContent=(chapter.index||'')+' · '+(chapter.title||'Глава')+
    ' ('+(chapter.text||'').length+' знаков)';
   scene.append(option);
  }
  if(result.chapters.some(c=>c.scene_id===old))scene.value=old;
 }catch(err){
  state.editorial.head=null;$(panel,'[data-chat-scene]').replaceChildren();
  $(panel,'[data-chat-head]').textContent='Нет выбранной редакции. '+err.message;
 }
}
async function recoverPreview(){
 const st=state.editorial,pid=projectId();if(!pid||!st.threadId||!st.lastProposal)return;
 try{
  const candidate=await req(prefix(pid)+'/chats/'+st.threadId+
   '/candidates/'+st.lastProposal);
  if(projectId()!==pid||candidate.status!=='proposed')return;
  st.pending=st.lastProposal;
  $(ui('editorial'),'[data-chat-diff]').textContent=candidate.diff;
  $(ui('editorial'),'[data-chat-preview]').hidden=false;
 }catch{/* No candidate yet; instruction may still be awaiting model. */}
}
const outlineChanges=(before,after)=>{
 const rows=[];
 for(const [label,base,next] of [
  ['Замысел',before?.brief||{},after?.brief||{}]
 ]){
  for(const key of Object.keys(next)){
   if(next[key]!==base[key])rows.push(label+' · '+key+'\\n  было: '+(base[key]||'—')+'\\n  станет: '+(next[key]||'—'));
  }
 }
 (after?.chapters||[]).forEach((chapter,i)=>{
  const current=before?.chapters?.[i]||{};
  for(const key of ['title','anchor','hook','tension','reveal_level','editorial_goal']){
   if(chapter[key]!==current[key]){
    rows.push('Глава '+(i+1)+' · '+key+'\\n  было: '+(current[key]??'—')+
      '\\n  станет: '+(chapter[key]??'—'));
   }
  }
 });
 return rows.length?rows.join('\\n\\n'):'Изменений нет';
};
async function refreshOutline(){
 const pid=projectId(),st=state.generation,panel=ui('generation');
 if(!pid||!st.threadId)return;
 const data=await req(prefix(pid)+'/chats/'+st.threadId+'/outline');
 if(projectId()!==pid)return;
 st.outline=data.latest?.status==='proposed'?data.latest.id:null;
 $(panel,'[data-chat-outline-preview]').hidden=!st.outline;
 if(st.outline){
  $(panel,'[data-chat-outline-diff]').textContent=outlineChanges(
    data.latest.current,data.latest.after);
  $(panel,'[data-chat-outline-status]').textContent=
   data.latest.source_unchanged?'Изменения ожидают вашего решения.':
   'Каркас изменился после предложения. Принятие заблокировано.';
 }else $(panel,'[data-chat-outline-status]').textContent=data.latest?
  'Последнее предложение: '+data.latest.status+'.':'Нет предложений. Сначала сохраните поручение.';
 return data;
}
async function proposeOutline(pid){
 const st=state.generation,panel=ui('generation');
 if(!st.lastProposal)throw Error('Сначала отправьте новое поручение в чате генерации.');
 const model=document.querySelector('#model')?.value;
 if(!model)throw Error('Не выбрана модель Ollama.');
 status('generation','Ollama готовит изменения каркаса. Никаких записей в книгу ещё нет.');
 const result=await post(prefix(pid)+'/chats/'+st.threadId+'/outline',{
   chat_proposal_id:st.lastProposal,model});
 if(projectId()!==pid)return;
 st.outline=result.id;
 $(panel,'[data-chat-outline-diff]').textContent=outlineChanges(result.before,result.after);
 $(panel,'[data-chat-outline-preview]').hidden=false;
 $(panel,'[data-chat-outline-status]').textContent='Предложение готово. Проверьте перед принятием.';
}
async function decideOutline(pid,approve){
 const st=state.generation,panel=ui('generation');
 if(!st.outline)throw Error('Нет предложенного каркаса.');
 if(approve&&!window.confirm(
  'Сохранить предложенные изменения в задании и плане? Несохранённые поля браузера нужно будет обновить из сервера. Текст глав не заменяется.'))return;
 const result=await post(prefix(pid)+'/chats/'+st.threadId+'/outline/'+st.outline+'/decision',
  {approve});
 if(projectId()!==pid)return;
 st.outline=null;
 $(panel,'[data-chat-outline-preview]').hidden=true;
 $(panel,'[data-chat-outline-status]').textContent=approve?
  'План сохранён. Перезагрузите рабочие поля для просмотра во вкладках 01–02.':
  'Предложение отклонено, исходное задание не менялось.';
 if(approve){
  // Reload canonical data in the already-open editor, including chapter plan.
  document.dispatchEvent(new CustomEvent('achewiw:chat-outline-approved',
    {detail:{projectId:pid}}));
 }
 await refreshOutline();
}
async function refreshBookJob(){
 const pid=projectId(),st=state.generation,panel=ui('generation');
 if(!pid||!st.threadId||st.projectId!==pid)return;
 const data=await req(prefix(pid)+'/chats/'+st.threadId+'/book-job');
 if(projectId()!==pid)return;
 const job=data.job;
 const label=$(panel,'[data-chat-book-status]');
 const prog=$(panel,'[data-chat-book-progress]');
 const start=$(panel,'[data-chat-book-start]');
 const pause=$(panel,'[data-chat-book-pause]');
 const resume=$(panel,'[data-chat-book-resume]');
 const open=$(panel,'[data-chat-book-open]');
 if(!job){
  label.textContent='Активной или незавершённой книги нет. Запуск использует сохранённый план из этапа 02.';
  prog.value=0;
  start.hidden=false;pause.hidden=true;resume.hidden=true;open.disabled=true;
 }else{
  const book=job.book||{};
  label.textContent=(job.stage||job.status)+' · '+(book.generated_words||0)+
   ' / '+(book.target_words||0)+' слов · глав '+(book.chapters_complete||0)+
   '/'+(book.chapters||0)+' · job '+job.id.slice(0,12);
  prog.value=Math.max(0,Math.min(100,Number(book.percent_words)||0));
  start.hidden=true;
  pause.hidden=!['queued','running'].includes(job.status)||Boolean(job.pause_requested);
  resume.hidden=!['interrupted','error'].includes(job.status);
  open.disabled=false;
 }
 return data;
}
async function controlBook(action,pid){
 const st=state.generation,model=document.querySelector('#model')?.value;
 if(action==='start'&&!model)throw Error('Выберите установленную модель в панели мастерской.');
 const labels={start:'Запустить написание книги по сохранённому каркасу?',
   pause:'Запросить безопасную паузу после текущего фрагмента?',
   resume:'Продолжить сохранённую книгу без удаления уже написанного?'};
 if(!window.confirm(labels[action]+' Действие использует существующую очередь ACHEWIW.'))return;
 status('generation','Выполняется действие над сохранённым заданием: '+action);
 const result=await post(prefix(pid)+'/chats/'+st.threadId+'/book-control',{
   action,confirm:true,...(action==='start'?{model}:{})
 });
 if(projectId()!==pid)return;
 await refreshBookJob();
 status('generation',action==='pause'?'Пауза запрошена. Текущий фрагмент будет сохранён.':
   'Действие выполнено. Книга остаётся в общей очереди ACHEWIW, job '+result.job.id.slice(0,12));
}
async function open(kind){
 if(!projectId())return;
 try{
  await thread(kind);
  if(kind==='editorial'){
   await versions();await head();await recoverPreview();
  }else{
   await refreshBookJob();await refreshOutline();
  }
  status(kind,'Диалог сохранён в SQLite. Изменения текста требуют отдельного принятия.');
 }catch(err){status(kind,'Ошибка загрузки: '+err.message)}
}
async function action(kind,task){
 const st=state[kind];if(!projectId()||st.busy)return;
 const pid=projectId(),panel=ui(kind);toggle(kind,true);
 try{
  await task(pid,panel,st);
 }catch(err){status(kind,'Ошибка: '+err.message)}
 finally{if(projectId()===pid)toggle(kind,false)}
}
async function send(kind,pid,panel,st){
 if(!st.threadId)await thread(kind);
 const message=$(panel,'[data-chat-message]').value.trim();
 if(!message)throw Error('Введите поручение.');
 const body={message};
 if(kind==='editorial'){
  if(!st.head)throw Error('Сначала выберите сохранённую редакцию.');
  const sceneId=$(panel,'[data-chat-scene]').value;
  if(!sceneId)throw Error('Выберите главу.');
  body.scope='chapter';body.scene_id=sceneId;body.base_version_id=st.head.version_id;
 }
 const response=await post(prefix(pid)+'/chats/'+st.threadId+'/messages',body);
 $(panel,'[data-chat-message]').value='';
 if(kind==='editorial'){
  st.lastProposal=response.proposal_id;
  st.pending=null;$(panel,'[data-chat-preview]').hidden=true;
 }
 const history=await req(prefix(pid)+'/chats/'+st.threadId);
 renderConversation(kind,history);
 status(kind,kind==='generation'?'Поручение сохранено. План распределён по этапам 01–06. Для запуска используй отдельную кнопку после утверждения каркаса.':
  'Поручение сохранено. Можете запросить предложение Ollama.');
}
async function generate(pid,panel,st){
 if(!st.lastProposal||!st.head)throw Error('Сначала сохраните поручение и выберите главу.');
 const scene=$(panel,'[data-chat-scene]').value;
 const selected=st.head.chapters.find(x=>x.scene_id===scene);
 if(!selected)throw Error('Глава не выбрана.');
 if(selected.text.length>4500)throw Error('Для первой проверки Ollama выберите главу до 4500 символов.');
 status('editorial','Ollama готовит предложение. Редакция не изменяется.');
 const modelEl=document.querySelector('#model');
 const model=modelEl?.value;
 if(!model)throw Error('Выберите установленную модель Ollama на панели мастерской.');
 const result=await post(prefix(pid)+'/chats/'+st.threadId+'/ollama-candidate',{
  chat_proposal_id:st.lastProposal,base_version_id:st.head.version_id,
  scene_id:scene,model
 });
 if(projectId()!==pid)return;
 st.pending=result.chat_proposal_id;
 $(panel,'[data-chat-diff]').textContent=result.diff;
 $(panel,'[data-chat-preview]').hidden=false;
 const note='Предложение готово'+(result.output_tokens!=null?' · '+result.output_tokens+' токенов':'')+
  '. Прочитайте сравнение и примите или отклоните.';
 status('editorial',note);
}
async function decide(pid,panel,st,approve){
 if(!st.pending)throw Error('Нет подготовленной редакции.');
 const id=st.pending;
 const result=await post(prefix(pid)+'/chats/'+st.threadId+'/candidates/'+id+'/decision',
  {approve});
 st.pending=null;st.lastProposal=null;$(panel,'[data-chat-preview]').hidden=true;
 await head();
 const history=await req(prefix(pid)+'/chats/'+st.threadId);
 renderConversation('editorial',history);
 status('editorial',approve?'Новая версия сохранена отдельно: '+result.new_immutable_version+
   '. Основной текст и DOCWORK не перезаписаны.':'Предложение отклонено. Исходник не изменён.');
}
document.querySelectorAll('[data-chat-workspace]').forEach(panel=>{
 const kind=panel.dataset.chatWorkspace;
 $(panel,'[data-chat-refresh]').addEventListener('click',()=>action(kind,async()=>{
  await open(kind);
 }));
 $(panel,'[data-chat-mode]').addEventListener('change',()=>action(kind,async(pid,_,st)=>{
  if(!st.threadId)await thread(kind);
  await put(prefix(pid)+'/chats/'+st.threadId+'/mode',{
   mode:$(panel,'[data-chat-mode]').value});
  await open(kind);
 }));
 $(panel,'[data-chat-send]').addEventListener('click',()=>action(kind,(pid,p,st)=>send(kind,pid,p,st)));
 if(kind==='generation'){
  $(panel,'[data-chat-outline-propose]').addEventListener('click',
   ()=>action(kind,(pid)=>proposeOutline(pid)));
  $(panel,'[data-chat-outline-refresh]').addEventListener('click',
   ()=>action(kind,refreshOutline));
  $(panel,'[data-chat-outline-approve]').addEventListener('click',
   ()=>action(kind,(pid)=>decideOutline(pid,true)));
  $(panel,'[data-chat-outline-reject]').addEventListener('click',
   ()=>action(kind,(pid)=>decideOutline(pid,false)));
  for(const control of ['start','pause','resume']){
   $(panel,'[data-chat-book-'+control+']').addEventListener('click',()=>action(kind,
    (pid)=>controlBook(control,pid)));
  }
  $(panel,'[data-chat-book-open]').addEventListener('click',()=>{
   const b=document.querySelector('[data-tab="book"]');
   if(b)b.click(); // Existing editor saves fields and opens canonical book pane.
  });
 }
 if(kind==='editorial'){
  $(panel,'[data-chat-load-versions]').addEventListener('click',()=>action(kind,versions));
  $(panel,'[data-chat-create-version]').addEventListener('click',()=>action(kind,async pid=>{
   const result=await post(prefix(pid)+'/editorial/versions',
    {source:'manuscript',label:'Снимок для чата редактуры'});
   await versions();
   $(panel,'[data-chat-version]').value=result.id;
   status(kind,'Снимок сохранён; нажмите «Выбрать снимок для работы».');
  }));
  $(panel,'[data-chat-attach]').addEventListener('click',()=>action(kind,async pid=>{
   const version=$(panel,'[data-chat-version]').value;
   if(!version)throw Error('Выберите редакцию.');
   const result=await post(prefix(pid)+'/chat-edition',{version_id:version});
   await head();
   if(result.already_attached)status(kind,'У этой книги уже есть рабочая редакция. Её нельзя молча заменить.');
   else status(kind,'Снимок подключён к редакторскому чату.');
  }));
  $(panel,'[data-chat-generate]').addEventListener('click',()=>action(kind,generate));
  $(panel,'[data-chat-approve]').addEventListener('click',()=>action(kind,(pid,p,st)=>decide(pid,p,st,true)));
  $(panel,'[data-chat-reject]').addEventListener('click',()=>action(kind,(pid,p,st)=>decide(pid,p,st,false)));
  $(panel,'[data-chat-scene]').addEventListener('change',()=>{
   state.editorial.lastProposal=null;state.editorial.pending=null;
   $(panel,'[data-chat-preview]').hidden=true;
   $(panel,'[data-chat-generate]').disabled=true;
  });
 }
});
// Display the same persistent job progress as 06; no second job worker.
setInterval(()=>{
 if(document.hidden||document.querySelector('#chat-generation')?.hidden||
    !projectId()||state.generation.busy||!state.generation.threadId)return;
 refreshBookJob().catch(error=>status('generation','Мониторинг: '+error.message));
},5000);
document.addEventListener('achewiw:tab-shown',event=>{
 const kind=event.detail?.name==='chat-generation'?'generation':
  event.detail?.name==='chat-editorial'?'editorial':null;
 if(kind)open(kind);
});
})();
