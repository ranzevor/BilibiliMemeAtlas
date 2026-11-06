'use strict';
const fs=require('node:fs');
const forbiddenCharacter=/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}\p{Default_Ignorable_Code_Point}]/u;
const nonstandardSpace=/\p{Zs}/u;
function hasUnsafeChars(value) {
  return forbiddenCharacter.test(value)||nonstandardSpace.test(value.replace(/ /gu,''));
}
function titleErrors(value) {
  const errors=[];
  if(typeof value!=="string"||!value.trim()) return ["提交或 PR 标题为空"];
  if(value!==value.normalize("NFC")) errors.push("标题必须采用 NFC 规范化文本");
  if(hasUnsafeChars(value)) errors.push("标题存在控制字符、不可见格式字符或非普通空格");
  const m=/^(feat|fix|docs|refactor|perf|test|build|ci|chore|style|revert)\(([a-z][a-z0-9]*(?:[-/][a-z0-9]+)*)\)(!)?: ([^\r\n]+)$/u.exec(value);
  if(!m) return [...errors,"标题须采用 type(scope): 简体中文具体变更主题"];
  const subject=m[4];
  if(m[2].length>36) errors.push("scope 过长");
  if(subject.length<8||subject.length>105) errors.push("主题没有清晰的长度边界");
  if(subject!==subject.trim()||/\s{2,}/u.test(subject)) errors.push("主题存在首尾或连续空白");
  if((subject.match(/\p{Script=Han}/gu)||[]).length<4) errors.push("主题中文行为或对象描述不足");
  if(/^(?:更新|优化|修复|调整|完善|修改|改进|处理|整理)(?:项目|代码|文件|问题|功能|内容|配置|逻辑|文档|测试|流程|实现)?$/u.test(subject)) errors.push("主题使用空泛描述");
  if(/\[(?:skip ci|ci skip)\]|\b(?:WIP|FIXME|TODO|TBD)\b/iu.test(subject)) errors.push("主题含跳过检查或占位标记");
  return errors;
}

function commitErrors(message) {
  if(typeof message!=="string"||!message) return ["提交消息为空"];
  const lines=message.replace(/\r\n/g,"\n").split("\n");
  const errors=titleErrors(lines[0]).map(e=>"提交："+e);
  if(lines.slice(1).some(hasUnsafeChars)) errors.push("提交正文存在控制字符、不可见格式字符或非普通空格");
  const body=lines.slice(1).join("\n").trim();
  const visibleBody=body.replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}\p{Default_Ignorable_Code_Point}\p{Zs}]/gu,"").replace(/\s/gu,"");
  const m=/^(feat|fix|docs|refactor|perf|test|build|ci|chore|style|revert)\([^)]+\)(!)?: /u.exec(lines[0]);
  if(m&&/^(?:feat|fix|refactor|perf)$/u.test(m[1])) {
    if(lines[1]!==""||visibleBody.length<24) errors.push("实质功能或缺陷变更需要空行分隔的有效正文");
  }
  if(body&&/^[ \t]*(?:(?:[-*+]|[0-9]+[.)])[ \t]+)?(?:\[[ xX]\][ \t]+)?(?:TODO|TBD|FIXME|待补充|占位|暂无内容|待填写|在此填写|请填写)(?=$|[ \t:：\-—])(?:[ \t]*[:：\-—]?[ \t]*[^\r\n]*)?$/imu.test(body)) errors.push("提交正文存在模板占位");
  if(m&&m[2]&&!/(?:^|\n)BREAKING CHANGE:\s*.*\p{Script=Han}/u.test(body)) errors.push("不兼容变更缺少中文 BREAKING CHANGE 说明");
  return errors;
}

let markdownParser;
function visibleMarkdownText(token) {
  return (token.children||[]).map(child=>
    child.type==='text'||child.type==='code_inline'?child.content:
    child.type==='softbreak'||child.type==='hardbreak'?'\n':''
  ).join('');
}
function htmlHasUnsafeDecodedText(content,decode) {
  const unsafe=value=>hasUnsafeChars(decode(value.replace(/\r\n?|\n/gu,'')));
  let i=0,rawTextTag='';
  while(i<content.length){
    if(rawTextTag){
      if(rawTextTag==='plaintext') return false;
      const close=new RegExp('</'+rawTextTag+'(?=[\\s/>])','giu');
      close.lastIndex=i;
      const match=close.exec(content);
      if(!match) return false;
      i=match.index;
      rawTextTag='';
    }
    if(content.startsWith('<!--',i)){
      const end=content.indexOf('-->',i+4);
      i=end<0?content.length:end+3;
      continue;
    }
    if(content.startsWith('<![CDATA[',i)){
      const end=content.indexOf(']]>',i+9);
      i=end<0?content.length:end+3;
      continue;
    }
    if(content.startsWith('<!',i)||content.startsWith('<?',i)){
      const end=content.indexOf('>',i+2);
      i=end<0?content.length:end+1;
      continue;
    }
    if(content[i]!=='<'){
      const next=content.indexOf('<',i);
      if(unsafe(content.slice(i,next<0?content.length:next))) return true;
      i=next<0?content.length:next;
      continue;
    }
    const tag=/^<(\/?)([A-Za-z][A-Za-z0-9:-]*)/u.exec(content.slice(i));
    if(!tag){i++;continue;}
    let end=i+tag[0].length,quote='';
    for(;end<content.length;end++){
      const ch=content[end];
      if(quote){if(ch===quote) quote='';}
      else if(ch==='"'||ch==="'") quote=ch;
      else if(ch==='>') break;
    }
    if(end===content.length) return unsafe(content.slice(i));
    if(!tag[1]){
      const attrs=content.slice(i+tag[0].length,end);
      const attribute=/[^\s"'<>/=]+(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'\x60=<>]+)))?/gu;
      for(const found of attrs.matchAll(attribute)){
        const value=found[1]??found[2]??found[3];
        if(value!==undefined&&unsafe(value)) return true;
      }
      if(/^(?:script|style|xmp|iframe|noembed|noframes|plaintext)$/iu.test(tag[2])&&!/\/\s*$/u.test(attrs))
        rawTextTag=tag[2].toLowerCase();
    }
    i=end+1;
  }
  return false;
}

function prBodyErrors(body) {
  if(typeof body!=='string') return ['PR 正文缺少有效内容'];
  if(body.length>65536) return ['PR 正文超过长度上限'];
  const errors=[];
  if(body.split(/\r?\n/u).some(hasUnsafeChars))
    errors.push('PR 正文存在控制字符、不可见格式字符或非普通空格');
  if(!markdownParser) markdownParser=new (require('markdown-it'))('commonmark',{html:true,linkify:false});
  const tokens=markdownParser.parse(body,{});
  const meaningful=value=>(value.match(/[\p{L}\p{N}]/gu)||[]).join('');
  const visibleBody=tokens.filter(t=>t.type==='inline').map(visibleMarkdownText).join('\n');
  // 字符实体在 Markdown 解析后才解码，需按实际文本再检查一次。
  if(visibleBody.split('\n').some(hasUnsafeChars))
    errors.push('PR 渲染后的正文存在控制字符、不可见格式字符或非普通空格');
  // 原始 HTML 与链接属性可通过实体解码生成不可见字符，不能只检查可见正文 token。
  const renderedTokens=tokens.flatMap(token=>[token,...(token.children||[])]);
  const unsafeHtmlOrAttributes=renderedTokens.some(token=>{
    const html=(token.type==='html_block'||token.type==='html_inline')
      && htmlHasUnsafeDecodedText(token.content,markdownParser.utils.unescapeAll);
    const attrs=(token.attrs||[]).some(([,value])=>
      typeof value==='string'&&hasUnsafeChars(value.replace(/\r\n?|\n/gu,'')));
    return html||attrs;
  });
  if(unsafeHtmlOrAttributes)
    errors.push('PR 渲染后的 HTML 或链接属性存在控制字符、不可见格式字符或非普通空格');
  if(meaningful(visibleBody).length<85)
    errors.push('PR 正文缺少有信息量的变更说明');
  if(/^\s*(?:[-*>]\s*)?(?:TODO|TBD|待补充|在此填写|请填写|placeholder)(?:\s*[:：].*)?\s*$/imu.test(visibleBody))
    errors.push('PR 正文包含未填模板占位');

  // 以顶层 Markdown 标题构造真实的章节层级；容器内标题不划分顶层章节。
  const headings=[];
  const headingInlinePositions=new Set();
  for(let i=0;i<tokens.length;i++){
    const token=tokens[i];
    if(token.type!=='heading_open'||!/^h[1-6]$/u.test(token.tag)) continue;
    headingInlinePositions.add(i+1);
    if(token.level===0)
      headings.push({h:visibleMarkdownText(tokens[i+1]),index:i,level:Number(token.tag[1])});
  }

  // 正文统计只使用可见的非标题文本；标题的字数不可充当章节说明。
  const textCounts=[0];
  for(let i=0;i<tokens.length;i++){
    const token=tokens[i];
    const count=token.type==='inline'&&!headingInlinePositions.has(i)
      ? meaningful(visibleMarkdownText(token)).length : 0;
    textCounts.push(textCounts[i]+count);
  }

  // 章节结束于下一个同级或更高层级标题；子节的正文归属于父章节。
  const sectionEnds=new Map();
  const stack=[];
  for(let i=headings.length-1;i>=0;i--){
    const heading=headings[i];
    while(stack.length&&stack[stack.length-1].level>heading.level) stack.pop();
    sectionEnds.set(heading.index,stack.length?stack[stack.length-1].index:tokens.length);
    stack.push(heading);
  }
  const groups={
    '背景与目标':/(?:背景|目标|动机)/u,
    '修改范围与实现':/(?:范围|修改|实现|变更|实施内容|修复内容)/u,
    '兼容性与风险':/(?:兼容|风险|影响|验收边界|适用边界)/u,
    '实际验证':/(?:验证|测试|回归|检查结果)/u,
    '未完成事项':/(?:未完成|待办|剩余|当前状态|已知问题|阻碍)/u
  };
  const entries=Object.entries(groups);
  const candidates=entries.map(([,pattern])=>
    headings.filter(x=>(x.level===2||x.level===3)&&pattern.test(x.h)));
  const usable=candidates.map(hits=>hits.filter(x=>
    textCounts[sectionEnds.get(x.index)]-textCounts[x.index+3]>=12));
  const assigned=new Map();
  function match(group,seen){
    for(const heading of usable[group]){
      if(seen.has(heading.index)) continue;
      seen.add(heading.index);
      const previous=assigned.get(heading.index);
      if(previous===undefined||match(previous,seen)){
        assigned.set(heading.index,group);
        return true;
      }
    }
    return false;
  }
  for(let i=0;i<entries.length;i++){
    if(match(i,new Set())) continue;
    const label=entries[i][0];
    errors.push(candidates[i].length&&!usable[i].length
      ? 'PR 章节缺少有效内容：'+label
      : 'PR 缺少可定位章节：'+label);
  }
  if(!errors.length){
    const byIndex=new Map(headings.map(heading=>[heading.index,heading]));
    const matched=[...assigned.keys()].map(index=>byIndex.get(index));
    // 普通子节的正文归属父节；被单独分配给另一必需章节的内容不能重复计入。
    function hasIndependentContent(selection){
      return selection.every(heading=>{
        const end=sectionEnds.get(heading.index);
        let count=textCounts[end]-textCounts[heading.index+3];
        for(const child of selection){
          if(child.index>heading.index&&child.index<end)
            count-=textCounts[sectionEnds.get(child.index)]-textCounts[child.index+3];
        }
        return count>=12;
      });
    }
    if(!hasIndependentContent(matched)){
      const order=[...usable.keys()].sort((a,b)=>usable[a].length-usable[b].length);
      const selection=[],used=new Set();
      let budget=100000;
      function assign(depth){
        if(depth===order.length) return true;
        for(const heading of usable[order[depth]]){
          if(--budget<0) return false;
          if(used.has(heading.index)) continue;
          selection.push(heading);
          used.add(heading.index);
          if(hasIndependentContent(selection)&&assign(depth+1)) return true;
          selection.pop();
          used.delete(heading.index);
        }
        return false;
      }
      if(!assign(0)) errors.push('PR 必需章节缺少独立有效正文，不能重复使用其他必需章节的内容');
    }
  }
  return errors;
}

function branchErrors(name) {
  if(typeof name!=="string"||!/^(?:feat|fix|docs|refactor|perf|test|build|ci|chore|style|revert|release|hotfix|feature|maintenance|quality|archive|automation|data|content|diagnostic|dependabot)\/[a-z0-9][a-z0-9._/-]{2,95}$/u.test(name))
    return ["PR 源分支命名缺少明确用途前缀与具体对象"];
  return [];
}

function prErrors(pr,commits,files) {
  const errors=titleErrors(pr&&pr.title).map(e=>"PR："+e);
  errors.push(...prBodyErrors(pr&&pr.body).map(e=>"PR："+e));
  errors.push(...branchErrors(pr&&pr.head&&pr.head.ref).map(e=>"PR："+e));
  if(!Array.isArray(commits)||!commits.length) errors.push("PR 缺少可核验提交列表");
  else {
    for(const item of commits){
      const message=item&&item.commit&&item.commit.message;
      errors.push(...commitErrors(message).map(e=>(item&&item.sha?item.sha.slice(0,12):"未知提交")+" "+e));
    }
    if(pr&&pr.head&&pr.head.sha&&commits[commits.length-1].sha!==pr.head.sha) errors.push("提交列表末尾与当前 HEAD 不一致");
  }
  if(!Array.isArray(files)||!files.length) errors.push("PR 缺少可核验的非空差异");
  else {
    if(files.length>500) errors.push("PR 变更超过 500 文件，必须拆分独立审查范围");
    for(const f of files) if(!f||typeof f.filename!=="string"||hasUnsafeChars(f.filename)) errors.push("文件名存在无效或不可见字符");
  }
  return errors;
}
async function ghList(path,token) {
  const all=[];
  for(let page=1;page<=11;page++){
    const response=await fetch('https://api.github.com'+path+(path.includes('?')?'&':'?')+'per_page=100&page='+page,{headers:{
      'Accept':'application/vnd.github+json',
      'X-GitHub-Api-Version':'2022-11-28',
      'Authorization':'Bearer '+token
    }});
    if(!response.ok) throw new Error('GitHub API 返回 '+response.status+'，查询：'+path);
    const list=await response.json();
    if(!Array.isArray(list)) throw new Error('GitHub API 响应不是列表');
    all.push(...list);
    if(list.length<100) return all;
  }
  throw new Error('提交或文件列表超出可验证范围');
}
async function ghGet(path,token) {
  const response=await fetch('https://api.github.com'+path,{headers:{
    'Accept':'application/vnd.github+json',
    'X-GitHub-Api-Version':'2022-11-28',
    'Authorization':'Bearer '+token
  }});
  if(!response.ok) throw new Error('GitHub API 返回 '+response.status+'，查询：'+path);
  return response.json();
}
function eventGitErrors(event){
  if(!event || !event.pull_request || !event.repository) throw new Error('缺少 PR 事件元数据');
  const pr=event.pull_request;
  const shaPattern=/^[0-9a-f]{40}$/u;
  if(!shaPattern.test(pr.head?.sha)||!shaPattern.test(pr.base?.sha)) throw new Error('PR SHA 无效');
  const exec=require('node:child_process').execFileSync;
  const git=(args,encoding='utf8')=>exec('git',args,{
    encoding,maxBuffer:16*1024*1024,windowsHide:true
  });
  const headSha=pr.head.sha,baseSha=pr.base.sha;
  const mergeBase=git(['rev-parse','HEAD^1']).trim();
  const mergeHead=git(['rev-parse','HEAD^2']).trim();
  if(mergeBase!==baseSha) throw new Error('检出对象的 PR BASE 与事件不一致');
  if(mergeHead!==headSha) throw new Error('检出对象的 PR HEAD 与事件不一致');
  git(['cat-file','-e',baseSha+'^{commit}']);
  git(['cat-file','-e',headSha+'^{commit}']);
  const ids=git(['rev-list','--reverse',baseSha+'..'+headSha]).trim().split(/\r?\n/u).filter(Boolean);
  if(!ids.length||ids.length>250||ids.some(id=>!shaPattern.test(id))) throw new Error('PR 提交列表无效或超出可核验范围');
  const commits=ids.map(id=>{
    const raw=git(['cat-file','commit',id]);
    const separator=raw.indexOf('\n\n');
    if(separator<0) throw new Error('无法解析 Git 提交对象');
    return {sha:id,commit:{message:raw.slice(separator+2)}};
  });
  const changed=git(['diff','--name-only','-z',baseSha+'...'+headSha],'buffer');
  const files=changed.toString('utf8').split('\0').filter(Boolean).map(filename=>({filename}));
  return prErrors(pr,commits,files);
}

function preparedHookMessage(message) {
  const exec=require('node:child_process').spawnSync;
  const options={encoding:'utf8',windowsHide:true,maxBuffer:16*1024*1024};
  const config=(key)=>{
    const result=exec('git',['config','--get',key],options);
    if(result.error||(result.status!==0&&result.status!==1))
      throw new Error('无法读取 Git 提交清理配置');
    return result.status===0?result.stdout.trim():null;
  };
  const mode=config('commit.cleanup')||'default';
  const edited=process.env.GIT_EDITOR!==':';
  const strip=mode==='strip'||(mode==='default'&&edited);
  if(!strip&&!(mode==='scissors'&&edited)) return message;
  let input=message;
  if(mode==='scissors'){
    const comment=config('core.commentChar')||'#';
    const lines=message.replace(/\r\n/gu,'\n').split('\n');
    const boundary=lines.indexOf(comment+' ------------------------ >8 ------------------------');
    if(boundary>=0) input=lines.slice(0,boundary).join('\n');
  }
  const args=strip?['stripspace','--strip-comments']:['stripspace'];
  const cleaned=exec('git',args,{...options,input});
  if(cleaned.error||cleaned.status!==0)
    throw new Error('无法按 Git 提交清理规则校验编辑器消息');
  return cleaned.stdout;
}

async function main(){
  const mode=process.argv[2];
  let errors=[];
  if(mode==='message-file'){
    if(!process.argv[3]) throw new Error('缺少 commit-msg 文件参数');
    errors=commitErrors(preparedHookMessage(fs.readFileSync(process.argv[3],'utf8')));
  }else if(mode==='preflight-json'){
    if(!process.argv[3]) throw new Error('缺少 PR 预检 JSON 文件参数');
    const input=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
    errors=prErrors(input.pull_request,input.commits,input.files);
  }else if(mode==='event-git'){
    const event=JSON.parse(fs.readFileSync(process.env.GITHUB_EVENT_PATH,'utf8'));
    errors=eventGitErrors(event);
  }else if(mode==='pr'){
    const event=JSON.parse(fs.readFileSync(process.env.GITHUB_EVENT_PATH,'utf8'));
    if(!event.pull_request||!event.repository) throw new Error('事件缺少 PR 身份');
    const token=process.env.GITHUB_TOKEN;
    if(!token) throw new Error('缺少 GitHub 读取令牌');
    const repo=event.repository.full_name;
    if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/u.test(repo)) throw new Error('仓库身份无效');
    const n=event.pull_request.number;
    if(!Number.isSafeInteger(n)||n<=0) throw new Error('PR 编号无效');
    const prefix='/repos/'+repo+'/pulls/'+n;
    const live=await ghGet(prefix,token);
    if(live.head.sha!==event.pull_request.head.sha) throw new Error('PR HEAD 已变化，拒绝使用过时数据');
    if(event.pull_request.base && live.base && live.base.ref!==event.pull_request.base.ref) throw new Error('PR 目标分支在事件后改变');
    const [commits,files]=await Promise.all([ghList(prefix+'/commits',token),ghList(prefix+'/files',token)]);
    const latest=await ghGet(prefix,token);
    if(latest.head.sha!==live.head.sha || (latest.base&&live.base&&latest.base.ref!==live.base.ref)) throw new Error('PR HEAD 或目标基线在校验期间改变');
    errors=prErrors(live,commits,files);
  }else throw new Error('不支持的检查模式');
  for(const problem of errors) console.error('FAIL: '+problem);
  if(errors.length) process.exitCode=1;
  else console.log('PASS: 提交与 PR 元数据校验通过');
}
module.exports={titleErrors,commitErrors,prBodyErrors,branchErrors,prErrors,eventGitErrors};
if(require.main===module) main().catch(e=>{console.error('FAIL: '+e.message);process.exitCode=1;});
