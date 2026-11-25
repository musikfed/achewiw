/* ACHEWIW 07/08: additive chat UI; existing manuscript and six panes untouched.
 * Owns only UI state + server-side chat records. No direct DOM HTML from Ollama.
 */
(()=>{
'use strict';
const workspaces=['generation','editorial'];
const state={
 generation:{projectId:null,threadId:null,busy:false},
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
async function open(kind){
 if(!projectId())return;
 try{
  await thread(kind);
  if(kind==='editorial'){
   await versions();await head();await recoverPreview();
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
 status(kind,kind==='generation'?'Поручение сохранено. План этапов показан в истории; генерация книги здесь пока не запускается.':
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
document.addEventListener('achewiw:tab-shown',event=>{
 const kind=event.detail?.name==='chat-generation'?'generation':
  event.detail?.name==='chat-editorial'?'editorial':null;
 if(kind)open(kind);
});
})();
