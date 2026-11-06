'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const cp=require('node:child_process');
const root=path.join(__dirname,'..');
const gate=path.join(root,'.github','scripts','governance_gate.cjs');
const mockSource="'use strict';\nconst mode=process.env.GOV_TEST_MODE;\nconst body=\"## 背景与目标\\n当前提交缺少元数据合规检查。\\n## 变更范围与实现\\n添加严格的中文提交标题和PR结构检查。\\n## 兼容性与风险\\n不影响业务代码；合并前仍然需要独立审查。\\n## 实际验证\\n已经执行符合规定和失败输入的合成正反例检查。\\n## 未完成事项\\n远端的规则与必需状态检查仍需配置和验证。\";\nconst validMessage=\"fix(api): 修正调用失败后的权限状态处理\\n\\n权限失败将终止本次请求，不再继续执行下游业务操作。\\n回归验证受保护路径、失败响应与已有正常流程，兼容当前请求契约。\";\nglobalThis.fetch=async function(url,opts){\nif(opts.headers.Authorization!=='Bearer synthetic-token') throw new Error('unexpected auth');\nconst path=new URL(url).pathname;\nif(mode==='api-fail') return {ok:false,status:403};\nif(path.endsWith('/pulls/9')) return {ok:true,json:async()=>({title:mode==='bad-title'?'fix(api): fix auth bug':'fix(api): 修正调用失败后的权限状态处理',body,head:{sha:'abc',ref:'fix/api-state'}})};\nif(path.endsWith('/pulls/9/commits')) return {ok:true,json:async()=>[{sha:'abc',commit:{message:mode==='bad-commit'?'fix(api): fix auth bug':validMessage}}]};\nif(path.endsWith('/pulls/9/files')) return {ok:true,json:async()=>mode==='no-files'?[]:[{filename:'src/api.ts'}]};\nreturn {ok:false,status:404};\n};";
const modes=[
  ['pass',0,'PASS:'],
  ['stale',1,'PR HEAD 已变化'],
  ['bad-title',1,'PR：'],
  ['bad-commit',1,'提交'],
  ['no-files',1,'PR 缺少可核验的非空差异'],
  ['api-fail',1,'GitHub API 返回 403']
];
test('读取 GitHub 实时 PR、完整提交列表并拒绝过时事件或无效响应',()=>{
  const work=fs.mkdtempSync(path.join(os.tmpdir(),'governance-check-'));
  try{
    const helper=path.join(work,'mock-api.cjs');
    const event=path.join(work,'event.json');
    fs.writeFileSync(helper,mockSource,{encoding:'utf8'});
    for(const [mode,expected,diagnostic] of modes){
      fs.writeFileSync(event,JSON.stringify({
        repository:{full_name:'example/repo'},
        pull_request:{number:9,head:{sha:mode==='stale'?'old':'abc'}}
      }),{encoding:'utf8'});
      const result=cp.spawnSync(process.execPath,['--require',helper,gate,'pr'],{
        cwd:root,encoding:'utf8',timeout:15000,
        env:{...process.env,GOV_TEST_MODE:mode,GITHUB_EVENT_PATH:event,GITHUB_TOKEN:'synthetic-token'}
      });
      assert.equal(result.status,expected,mode+': '+result.stdout+result.stderr);
      assert.ok((result.stdout+result.stderr).includes(diagnostic),mode+': '+result.stdout+result.stderr);
    }
  }finally{
    fs.rmSync(work,{recursive:true,force:true});
  }
});
