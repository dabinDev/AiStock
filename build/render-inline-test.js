function escapeHtml(text){
  return String(text).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
}
function renderInlineMarkdown(text){
  const tokens=[];
  let html=escapeHtml(String(text||''));
  html=html.replace(/`([^`]+)`/g,(_,code)=>{
    const token='@@CODETOKEN'+tokens.length+'@@';
    tokens.push('<code>'+code+'</code>');
    return token;
  });
  html=html.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  html=html.replace(/__([^_]+)__/g,'<strong>$1</strong>');
  html=html.replace(/\*([^*]+)\*/g,'<em>$1</em>');
  html=html.replace(/_([^_]+)_/g,'<em>$1</em>');
  html=html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  tokens.forEach((tokenHtml,index)=>{
    html=html.split('@@CODETOKEN'+index+'@@').join(tokenHtml);
  });
  return html;
}
console.log(renderInlineMarkdown('当前证据指向 `修复预期` 向 `分歧预期` 过渡'));
