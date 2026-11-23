/* ACHEWIW R3.7F — untrusted vision proposal review. No image/model calls. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.ACHEWIWVisionProposals=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const kinds=new Set(['rectangle','ellipse','line','arrow','text']);
  function validate(raw,scope){
    if(!scope||!Number.isInteger(scope.width)||!Number.isInteger(scope.height)||
        !scope.draftId||!Number.isInteger(scope.pictureIndex)||
        typeof scope.baseAsset!=='string')throw Error('Нет контекста рисунка');
    if(!raw||raw.format!=='achewiw-vision-proposal'||raw.version!==1||
        raw.status!=='unverified'||raw.autoApply!==false||
        !raw.documentScope||raw.documentScope.draftId!==scope.draftId||
        raw.documentScope.pictureIndex!==scope.pictureIndex||
        raw.documentScope.baseAsset!==scope.baseAsset||
        raw.canvas?.width!==scope.width||raw.canvas?.height!==scope.height)
      throw Error('Предложение не относится к текущему рисунку или формат неизвестен');
    if(!Array.isArray(raw.proposals)||raw.proposals.length>100)
      throw Error('Некорректное количество предположений');
    const ids=new Set();
    const clean=raw.proposals.map(p=>{
      if(!p||typeof p.id!=='string'||!/^candidate-[1-9][0-9]*$/.test(p.id)||
         ids.has(p.id)||!kinds.has(p.type)||typeof p.confidence!=='number'||
         !Number.isFinite(p.confidence)||p.confidence<0||p.confidence>1||
         !['unverified','uncertain'].includes(p.verification)||
         ![p.x,p.y,p.w,p.h].every(v=>typeof v==='number'&&Number.isFinite(v)&&
            v>=-16000000&&v<=16000000))
        throw Error('Неверное предположение');
      if(p.type==='text'&&(typeof p.text!=='string'||p.text.length>180))
        throw Error('Неверная предложенная надпись');
      ids.add(p.id);
      return {id:p.id,type:p.type,confidence:p.confidence,verification:p.verification,
        x:p.x,y:p.y,w:p.w,h:p.h,...(p.type==='text'?{text:p.text}:{})};
    });
    return {proposals:clean,count:clean.length};
  }
  function toManualShape(candidate,scope){
    if(!candidate||!scope||!Number.isInteger(scope.width)||!Number.isInteger(scope.height))
      throw Error('Недостаточно данных для ручного принятия');
    const geometry=[candidate.x,candidate.y,candidate.w,candidate.h];
    if(!geometry.every(v=>typeof v==='number'&&Number.isFinite(v)))
      throw Error('Некорректная геометрия');
    if(candidate.x<0||candidate.y<0||
       candidate.x+candidate.w>scope.width||
       candidate.y+candidate.h>scope.height)
      throw Error('Предложение выходит за границы рисунка');
    if(['rectangle','ellipse','line','arrow'].includes(candidate.type) &&
       (Math.abs(candidate.w)<2||Math.abs(candidate.h)<2 &&
         !['line','arrow'].includes(candidate.type)))
      throw Error('Слишком маленькая фигура');
    if(candidate.type==='text'&&(!candidate.text||candidate.text.trim()===''))
      throw Error('Пустую подпись нельзя принять');
    if(!kinds.has(candidate.type))throw Error('Неизвестная фигура');
    const result={type:candidate.type,x:candidate.x,y:candidate.y,
      w:candidate.w,h:candidate.h,color:'#176d47',stroke:3};
    if(candidate.type==='text'){
      result.text=candidate.text;
      result.fontSize:26;
    }
    return result;
  }
  return {validate,toManualShape};
});
