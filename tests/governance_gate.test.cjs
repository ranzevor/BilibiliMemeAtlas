'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const gate=require('../.github/scripts/governance_gate.cjs');
const body="## 背景与目标\n需要阻断不合规范的变更入口。\n## 变更范围与实现\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。\n## 兼容性与风险\n不改变产品功能；尚需配置必需检查才能阻断合并。\n## 实际验证\n完成了符合要求与违规请求的本地正反例测试。\n## 未完成事项\n尚需核对远端检查来源并配置有效的 GitHub 规则。";
const commit="feat(governance): 新增提交和PR元数据的规则校验\n\n针对提交与PR的格式和说明不足增加严格检查。\n验证合规中文主题、缺失范围与控制字符反例，保留既有业务功能。";
test('合法中文主题及技术标识混用',()=>{
  assert.deepEqual(gate.titleErrors('fix(references): 核实⑨的官方出处与早期站内用例'),[]);
  assert.deepEqual(gate.titleErrors('fix(api): 修正 API 鉴权失败后的响应状态'),[]);
});
test('拒绝纯英文、无scope、空泛中文和不可见字符',()=>{
  for(const title of ['fix(api): fix login error','fix: 修复用户验证状态错误','fix(api): 优化代码','fix(api): 修复鉴权\u200b状态转移错误'])
    assert.notEqual(gate.titleErrors(title).length,0,title);
});
test('重要提交缺正文以及不兼容说明缺失',()=>{
  assert.deepEqual(gate.commitErrors(commit),[]);
  assert.notEqual(gate.commitErrors('feat(auth): 新增账户权限校验流程').length,0);
  assert.notEqual(gate.commitErrors('feat(api)!: 调整客户端消息契约\n\n调整旧版客户端的协议读取行为并要求迁移。').length,0);
});
test('提交正文的真实占位行不能靠其他说明长度绕过',()=>{
  const subject='fix(api): 修正请求失败后的错误恢复';
  const details='针对请求失败后的恢复状态进行修正，并覆盖既有客户端的正常与异常交互。';
  for(const placeholder of [
    'TODO: 补充变更影响与测试结果',
    'TODO：补充变更影响与测试结果',
    'TBD: 填写验收结论',
    'FIXME - 补充兼容影响',
    '待补充：具体的修改说明',
    '占位',
    '暂无内容',
    '- TODO: 补充错误恢复的实际验证',
    '- [ ] TODO: 核查回归情况',
    '1. TBD：填写测试结果',
    '请填写：本次兼容性影响'
  ]){
    const errors=gate.commitErrors(subject+'\n\n'+details+'\n'+placeholder);
    assert(errors.some(e=>e.includes('模板占位')),placeholder+' '+JSON.stringify(errors));
  }
  for(const detail of [
    details+' 原有 TODO: 标记现已替换为明确的状态记录。',
    details+' 已解释代码中 FIXME: 备注的迁移过程。',
    details+' - 验证通过后，记录实际测试结果与兼容情况。'
  ])
    assert.deepEqual(gate.commitErrors(subject+'\n\n'+detail),[],detail);
});

test('PR 正文必须包含实质章节',()=>{
  assert.deepEqual(gate.prBodyErrors(body),[]);
  assert.notEqual(gate.prBodyErrors('## 背景与目标\n修复问题').length,0);
});
test('PR 分支和 HEAD 一致性',()=>{
  assert.deepEqual(gate.branchErrors('feat/governance-pr-metadata-gate'),[]);
  assert.notEqual(gate.branchErrors('my-work').length,0);
  const issues=gate.prErrors({title:'fix(api): 修正 API 鉴权失败后的响应状态',body,head:{ref:'fix/api-response',sha:'def'}},[{sha:'abc',commit:{message:commit}}],[{filename:'src/api.py'}]);
  assert(issues.some(x=>x.includes('HEAD')));
});


test('模板占位及代码围栏、HTML 注释伪章节不得充当 PR 实际验证',()=>{
  const sample="## 背景与目标\n需要阻断不合规范的变更入口。\n## 变更范围与实现\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。\n## 兼容性与风险\n不改变产品功能；尚需配置必需检查才能阻断合并。\n## 实际验证\n完成了符合要求与违规请求的本地正反例测试。\n## 未完成事项\n尚需核对远端检查来源并配置有效的 GitHub 规则。";
  assert.deepEqual(gate.prBodyErrors(sample),[]);
  assert(gate.prBodyErrors(sample.replace("需要阻断不合规范的变更入口。","TODO: 补充本次具体网页行为目标。")).some(x=>x.includes('占位')));
  assert(gate.prBodyErrors(sample.replace("## 实际验证","```md\n## 实际验证\n```\n## 运行情况")).some(x=>x.includes('实际验证')));
  assert(gate.prBodyErrors(sample.replace("## 实际验证","<!--\n## 实际验证\n-->\n## 运行情况")).some(x=>x.includes('实际验证')));
});


test('准确中文行为不受有限动词词表误拒绝',()=>{
  assert.deepEqual(gate.titleErrors('fix(governance): 将治理文件逐项纳入项目文件安全白名单'),[]);
  assert.deepEqual(gate.titleErrors('fix(governance): 复用既有PR矩阵完成无令牌提交核验'),[]);
  assert.deepEqual(gate.titleErrors('fix(governance): 核对提交元数据与当前交付边界'),[]);
});
test('Markdown 围栏内带语言的三反引号不应关闭当前围栏',()=>{
  const tick=String.fromCharCode(96).repeat(3);
  const sample=body.replace('## 实际验证',tick+'md\n'+tick+'js\n## 实际验证');
  const errors=gate.prBodyErrors(sample);
  assert(errors.some(x=>x.includes('实际验证')),JSON.stringify(errors));
  assert(errors.some(x=>x.includes('未完成事项')),JSON.stringify(errors));
  assert.deepEqual(gate.prBodyErrors(body+'\n'+tick+'js\n## 非正文章节\n'+tick),[]);
  assert.deepEqual(gate.prBodyErrors(body+'\n~~~~md\n'+tick+'js\n~~~~'),[]);
});
test('不可见正文不能满足章节长度、提交说明或标题格式',()=>{
  const invisible='\u200B'.repeat(30);
  const labels=['背景与目标','修改范围与实现','兼容性与风险','实际验证','未完成事项'];
  const onlyInvisible=labels.map(label=>'## '+label+'\n'+invisible).join('\n');
  const errors=gate.prBodyErrors(onlyInvisible);
  assert(errors.some(e=>e.includes('格式字符')));
  for(const label of labels) assert(errors.some(e=>e.includes('章节缺少有效内容：'+label)),label);
  assert(gate.commitErrors('fix(api): 修正请求失败后的错误恢复\n\n'+invisible).some(e=>e.includes('有效正文')));
  assert(gate.commitErrors(commit+'\n\u202E').some(e=>e.includes('控制字符')));
  assert(gate.prBodyErrors(body+'\n\u202E').some(e=>e.includes('控制字符')));
});
test('中文普通空格合法，特殊空格和格式控制字符拒绝',()=>{
  assert.deepEqual(gate.titleErrors('fix(api): 修正 API 鉴权失败后的响应状态'),[]);
  assert.deepEqual(gate.commitErrors(commit),[]);
  assert.deepEqual(gate.prBodyErrors(body),[]);
  for(const c of ['\u00A0','\u3000','\u202F']){
    assert(gate.titleErrors('fix(api): 修正鉴权'+c+'失败后的状态处理').length>0);
    assert(gate.commitErrors(commit+'\n兼容'+c+'旧版').length>0);
    assert(gate.prBodyErrors(body+'\n兼容'+c+'旧版').length>0);
  }
});
test('合法 Markdown 内联代码中的 HTML 注释起始符不会吞掉章节',()=>{
  const sample=body.replace('需要阻断不合规范的变更入口。','需要阻断不合规范的变更入口，并展示内联代码 `<!--`。');
  assert.deepEqual(gate.prBodyErrors(sample),[]);
  assert.deepEqual(gate.prBodyErrors(body.replace('需要阻断不合规范的变更入口。','需要阻断不合规范的变更入口，并展示内联代码 ``<!--``。')),[]);
});
test('同一标题不能冒充全部五个 PR 章节',()=>{
  const forged='## 背景目标修改范围实现兼容风险实际验证未完成事项\n'
    +'本次修改实际影响与检查内容已按照要求进行描述'.repeat(6);
  const errors=gate.prBodyErrors(forged);
  for(const label of ['修改范围与实现','兼容性与风险','实际验证','未完成事项'])
    assert(errors.some(x=>x.includes('缺少可定位章节：'+label)),label);
});

test('HTML 原始预格式化块不得冒充必需章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const fake=body.replace(section,'<pre>\n'+section+'\n</pre>');
  assert(gate.prBodyErrors(fake).some(x=>x.includes('缺少可定位章节：实际验证')));
  const neighbor=body.replace('## 实际验证','<pre class="example">\n## 示例中的标题\n</pre>\n## 实际验证');
  assert.deepEqual(gate.prBodyErrors(neighbor),[]);
  for(const tag of ['script','style','textarea']){
    const invalid=body.replace(section,'<'+tag+'>\n'+section+'\n</'+tag+'>');
    assert(gate.prBodyErrors(invalid).some(x=>x.includes('缺少可定位章节：实际验证')),tag);
  }
});
test('合法代码围栏信息字符串中的注释符号不影响真实章节',()=>{
  const sample=body.replace('## 实际验证','```md<!--\n## 围栏中的示例标题\n```\n## 实际验证');
  assert.deepEqual(gate.prBodyErrors(sample),[]);
  const missing=body.replace('## 实际验证\n完成了符合要求与违规请求的本地正反例测试。',
    '```md<!--\n## 实际验证\n完成了符合要求与违规请求的本地正反例测试。\n```');
  assert(gate.prBodyErrors(missing).some(x=>x.includes('缺少可定位章节：实际验证')));
});
test('重叠关键词的五个真实章节可重新分配实现一对一匹配',()=>{
  const ambiguous=body.replace('## 背景与目标','## 背景与修改范围')
    .replace('## 变更范围与实现','## 背景与目标');
  assert.deepEqual(gate.prBodyErrors(ambiguous),[]);
});

test('原始 HTML 块中的标题不能冒充必需章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const tag of ['div','table','section','details']){
    const fake=body.replace(section,'<'+tag+'>\n'+section+'\n</'+tag+'>\n');
    assert(gate.prBodyErrors(fake).some(x=>x.includes('缺少可定位章节：实际验证')),tag);
    const valid=body.replace(section,'<'+tag+'>\n## 示例标题\n</'+tag+'>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],tag);
  }
  for(const opening of ['<custom-block>','<?php','<![CDATA[','<!DOCTYPE html']){
    const closing={'<custom-block>':'</custom-block>','<?php':'?>','<![CDATA[':']]>','<!DOCTYPE html':'>'}[opening];
    const fake=body.replace(section,'\n\n'+opening+'\n'+section+'\n'+closing+'\n');
    assert(gate.prBodyErrors(fake).some(x=>x.includes('缺少可定位章节：实际验证')),opening);
  }
  const inline=body.replace('需要阻断不合规范的变更入口。',
    '需要阻断不合规范的变更入口。\n<span>\n这里的标签属于正常段落内容，不应吞掉后续真实章节。');
  assert.deepEqual(gate.prBodyErrors(inline),[]);
});
test('无文本 HTML 标记与字符实体不构成章节说明',()=>{
  const labels=['背景与目标','修改范围与实现','兼容性与风险','实际验证','未完成事项'];
  for(const markup of ['<span></span>'.repeat(6),'<span title="属性不是说明文字"></span>'.repeat(6),'&nbsp;'.repeat(30)]){
    const fake=labels.map(label=>'## '+label+'\n'+markup).join('\n');
    const errors=gate.prBodyErrors(fake);
    for(const label of labels) assert(errors.some(x=>x.includes('章节缺少有效内容：'+label)),label);
  }
  const valid=body.replace('需要阻断不合规范的变更入口。',
    '需要阻断不合规范的变更入口，并采用 <span class="description">明确的用户行为说明</span>。');
  assert.deepEqual(gate.prBodyErrors(valid),[]);
});

test('注释与 Markdown 分隔线后原始 HTML 块不得伪造 PR 章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const separator of ['<!-- 注释 -->','---','* * *','_ _ _']){
    const fake=body.replace(section,'\n'+separator+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(fake).some(x=>x.includes('缺少可定位章节：实际验证')),separator);
    const valid=body.replace(section,'\n'+separator+'\n<custom-block>\n## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],separator);
  }
});

test('链接引用定义和缩进代码后的 HTML 原始块不构成章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const preceding of ['[link]: /reference','    缩进代码中的示例内容']){
    const forged=body.replace(section,'\n'+preceding+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(x=>x.includes('缺少可定位章节：实际验证')),preceding);
    const legal=body.replace(section,'\n'+preceding+'\n<custom-block>\n## 示例中的标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(legal),[],preceding);
  }
});
test('转义注释标记与跨行内联代码不影响真实章节',()=>{
  const escaped=String.fromCharCode(92)+'<!--';
  assert.deepEqual(gate.prBodyErrors(body.replace('## 实际验证',escaped+'\n## 实际验证')),[]);
  const tick=String.fromCharCode(96);
  const multiline=body.replace('## 实际验证','示例包含 '+tick+'\n跨行代码中的 <!-- 标记\n'+tick+' 是正常文本。\n## 实际验证');
  assert.deepEqual(gate.prBodyErrors(multiline),[]);
  const missing=body.replace('## 实际验证\n完成了符合要求与违规请求的本地正反例测试。',
    '<!--\n## 实际验证\n完成了符合要求与违规请求的本地正反例测试。\n-->');
  assert(gate.prBodyErrors(missing).some(x=>x.includes('缺少可定位章节：实际验证')));
});
test('跨行代码跨度不能越过 Markdown 标题边界',()=>{
  const tick=String.fromCharCode(96);
  const legal=body.replace('## 实际验证','正文中未配对的 '+tick+' 是普通文本。\n## 实际验证')
    .replace('## 未完成事项','代码示例 '+tick+'text'+tick+' 正常显示。\n## 未完成事项');
  assert.deepEqual(gate.prBodyErrors(legal),[]);
});

test('多行引用标题与转义标签后的原始 HTML 不能伪造章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const definition of ['[ref]: /url\n  "引用标题"','[ref\\]]: /url','[ref\\]]: /url\n  "引用标题"']){
    const forged=body.replace(section,'\n\n'+definition+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const valid=body.replace(section,'\n\n'+definition+'\n<custom-block>\n## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],definition);
  }
});
test('Setext 标题结束段落后原始 HTML 不得伪造章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const underline of ['===','---','  ===']){
    const prefix='\n\n示例标题\n'+underline+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),underline);
    const valid=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],underline);
  }
});
test('非法链接引用定义不能阻断合法标题',()=>{
  for(const malformed of ['[foo[bar]: /ref','[ ]: /ref']){
    const valid=body.replace('## 实际验证','\n\n'+malformed+'\n<custom-block>\n## 实际验证');
    assert.deepEqual(gate.prBodyErrors(valid),[],malformed);
  }
});
test('URL 和邮箱自动链接不是独立原始 HTML 块',()=>{
  for(const autolink of ['<https://example.com>','<support@example.com>']){
    const valid=body.replace('## 实际验证','\n\n'+autolink+'\n## 实际验证');
    assert.deepEqual(gate.prBodyErrors(valid),[],autolink);
  }
});


test('引用定义的转义标题与跨行目标不能允许伪造章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const definitions=[
    '[ref]: /url "a\\"b"',
    '[ref]:\n  /url',
    '[ref]: /url\n  "引用\n  标题"',
    '[ref]: /url\n  "引用标题"',
    '[foo\\bar]: /url',
    '[foo\\]]: /url'
  ];
  for(const definition of definitions){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const legal=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(legal),[],definition);
  }
});

test('空列表项后的 HTML 块不能提供必需章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const marker of ['-','*','+','1.']){
    const prefix='\n\n'+marker+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),marker);
    const valid=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],marker);
  }
});

test('ATX 标题允许零至三个空格缩进但拒绝四个空格代码',()=>{
  for(let indent=0;indent<=4;indent++){
    const sample=body.replace('## 实际验证',' '.repeat(indent)+'## 实际验证');
    const errors=gate.prBodyErrors(sample);
    if(indent===4) assert(errors.some(e=>e.includes('缺少可定位章节：实际验证')));
    else assert.deepEqual(errors,[],String(indent));
  }
});

test('跨行 track 原始 HTML 块中的标题不能冒充必需章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const opening='<track kind="captions"\nsrc="test.vtt">';
  const prefix='\n\n'+opening+'\n';
  const forged=body.replace(section,prefix+section+'\n</track>\n');
  assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')));
  const legal=body.replace(section,prefix+'## 示例标题\n</track>\n\n'+section);
  assert.deepEqual(gate.prBodyErrors(legal),[]);
});


test('跨行链接引用目标与标题允许行首及四格缩进',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const definition of ['[ref]:\n/url','[ref]:\n    /url','[ref]: /url\n"标题"']){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const legal=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(legal),[],definition);
  }
});

test('非空列表项保持段落而空列表项不能允许原始 HTML 伪标题',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const item of ['- 已完成相关修改','2. 已完成相关修改']){
    const valid=body.replace(section,'\n\n'+item+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert.deepEqual(gate.prBodyErrors(valid),[],item);
  }
  for(const item of ['-','2.']){
    const forged=body.replace(section,'\n\n'+item+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),item);
  }
});

test('未闭合的跨行引用标题不能吞掉真实 ATX 标题',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const malformed=body.replace(section,'\n\n[ref]: /url\n  "引用开始\n'+section+'\n"');
  assert.deepEqual(gate.prBodyErrors(malformed),[]);
  const validDefinition='[ref]: /url\n  "引用\n  标题"';
  const forged=body.replace(section,'\n\n'+validDefinition+'\n<custom-block>\n'+section+'\n</custom-block>\n');
  assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')));
});

test('引用标题中的等号文本与列表边界符合 Markdown 语义',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const underline of ['===','  ===']){
    const definition='[ref]: /url\n  "引用\n'+underline+'\n  标题"';
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),underline);
    const legal=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(legal),[],underline);
  }
  for(const marker of ['1. 条目','2. 条目','- 条目']){
    const valid=body.replace(section,'\n\n[ref]: /url\n  "引用\n'+marker+'\n  标题"\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert.deepEqual(gate.prBodyErrors(valid),[],marker);
  }
});

test('跨行引用标签完整参与引用定义，HTML 块中的标题不能冒充章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const definitions=[
    '[\nref\n]: /url',
    '[\n ref\n ]: /url',
    '[re\nf]: /url',
    '[ref\\]\npart]: /url',
    '[\nref\n]:\n/url',
    '[\nref\n]: /url\n  "标题"'
  ];
  for(const definition of definitions){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const valid=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],definition);
  }
  const nonDefinition=body.replace(section,'\n\n[\n\nref\n]: /url\n'+section);
  assert.deepEqual(gate.prBodyErrors(nonDefinition),[]);
});

test('空块引用不会维持段落并放行后续 HTML 块伪章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const quote of ['>','> ','   >   ','> >','>\n>  ']){
    const prefix='\n\n'+quote+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),quote);
    const valid=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],quote);
  }
  assert.deepEqual(gate.prBodyErrors(body.replace(section,'\n\n>\n'+section)),[]);
  assert.deepEqual(gate.prBodyErrors(body.replace(section,
    '\n\n> 引用正文\n<custom-block>\n'+section+'\n</custom-block>\n')),[]);
});


test('跨行引用标签遇到真实块级标题时不能吞掉章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const heading of ['## 实际验证','  ## 实际验证']){
    const legal=body.replace(section,'\n\n[\n'+heading+'\n]: /url\n完成了符合要求与违规请求的本地正反例测试。');
    assert.deepEqual(gate.prBodyErrors(legal),[],heading);
  }
  for(const definition of ['[\nref\n]: /url','[\n  ref\n]: /url','[\n===\n]: /url']){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
  }
});

test('容器中的引用定义不能使原始 HTML 伪章节通过',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const definition of ['> [ref]: /url','- [ref]: /url','* [ref]: /url','+ [ref]: /url','1. [ref]: /url','> > [ref]: /url','- [ref]: /url "引用标题"']){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const valid=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],definition);
  }
  for(const normal of ['> 引用正文','- 列表项正文','1. 列表项正文','> [ref]: /url 额外文本']){
    const valid=body.replace(section,'\n\n'+normal+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert.deepEqual(gate.prBodyErrors(valid),[],normal);
  }
});
test('容器定义只在新块起始时生效',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const normal of [
    '先前段落\n2. [ref]: /url',
    '> 先前段落\n> [ref]: /url',
    '> > 先前段落\n> [ref]: /url',
    '> 先前段落\n> 2. [ref]: /url'
  ]){
    const valid=body.replace(section,'\n\n'+normal+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert.deepEqual(gate.prBodyErrors(valid),[],normal);
  }
  for(const definition of [
    '先前段落\n1. [ref]: /url',
    '> 先前段落\n> - [ref]: /url',
    '> 先前段落\n> > [ref]: /url',
    '1. 先前条目\n2. [ref]: /url'
  ]){
    const forged=body.replace(section,'\n\n'+definition+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
  }
});

test('容器内跨行引用目标、标签和标题不得伪造章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const definitions=[
    '> [ref]: /url\n> "标题"',
    '- [ref]: /url\n  "标题"',
    '- [ref]:\n  /url',
    '> [ref]:\n> /url',
    '> [ref\n> ]: /url',
    '- [ref]: /url\n  "引用\n  标题"',
    '> [ref]: /url\n> "引用\n> 标题"',
    '- [ref]: /url\n"标题"'
  ];
  for(const definition of definitions){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const forged=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(forged).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const legal=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(legal),[],definition);
  }
  for(const ordinary of ['- [ref]: /url\n  普通文本','> [ref]: /url\n> 普通文本']){
    const legal=body.replace(section,'\n\n'+ordinary+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert.deepEqual(gate.prBodyErrors(legal),[],ordinary);
  }
});

test('混合嵌套容器的跨行引用必须完整消费并阻断 HTML 伪章节',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const definitions=[
    '- > [ref]: /url\n  > "标题"',
    '* > [ref]: /url\n  > "标题"',
    '+ > [ref]: /url\n  > "标题"',
    '1. > [ref]: /url\n   > "标题"',
    '10. > [ref]: /url\n    > "标题"',
    '-   > [ref]: /url\n    > "标题"',
    '> - > [ref]: /url\n>   > "标题"',
    '> * > [ref]: /url\n>   > "标题"',
    '> 1. > [ref]: /url\n>    > "标题"',
    '- > [ref]:\n  > /url',
    '- > [ref\n  > ]: /url',
    '> - > [ref]:\n>   > /url',
    '> - > [ref\n>   > ]: /url',
    '- > [ref]: /url\n  > "引用\n  > 标题"'
  ];
  for(const definition of definitions){
    const prefix='\n\n'+definition+'\n<custom-block>\n';
    const invalid=body.replace(section,prefix+section+'\n</custom-block>\n');
    assert(gate.prBodyErrors(invalid).some(e=>e.includes('缺少可定位章节：实际验证')),definition);
    const valid=body.replace(section,prefix+'## 示例标题\n</custom-block>\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(valid),[],definition);
  }
  for(const ordinary of [
    '- > 普通引用正文\n  > 连续内容',
    '> - > 普通引用正文\n>   > 连续内容',
    '- > [ref]: /url\n  > 普通文本'
  ]){
    const valid=body.replace(section,'\n\n'+ordinary+'\n<custom-block>\n'+section+'\n</custom-block>\n');
    assert.deepEqual(gate.prBodyErrors(valid),[],ordinary);
  }
});


test('CommonMark 块级结构在多重容器与引用变体中保持章节语义',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const definitions=[
    '[ref]: /url',
    '[ref]:\n/url',
    '[\nref\n]: /url',
    '[ref]: /url\n"跨行\n标题"',
    '> [ref]: /url',
    '- [ref]: /url',
    '- > [ref]: /url\n  > "标题"',
    '> - > [ref]: /url\n>   > "标题"',
    '- > - > [ref]: /url\n  >   > "标题"',
    '> - > - > [ref]: /url\n>   >   > "标题"',
    '1. > [ref]:\n   > /url',
    '* > [ref\n  > ]: /url',
    '- > [ref]: /url\n  > "标题\n  > 第二行"'
  ];
  for(const definition of definitions){
    for(const [open,close] of [
      ['<custom-block>','</custom-block>'],['<div>','</div>'],
      ['<!--','-->'],['<pre>','</pre>']
    ]){
      const forged=body.replace(section,'\n\n'+definition+'\n'+open+'\n'+section+'\n'+close+'\n');
      const errors=gate.prBodyErrors(forged);
      assert(errors.some(e=>e.includes('缺少可定位章节：实际验证')),definition+' '+open+': '+JSON.stringify(errors));
      const valid=body.replace(section,'\n\n'+definition+'\n'+open+'\n## 示例标题\n'+close+'\n\n'+section);
      assert.deepEqual(gate.prBodyErrors(valid),[],definition+' '+open);
    }
  }
});
test('章节识别使用真实 Markdown 标题节点与可见说明文本',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  for(const prelude of [
    '~~~md\n## 实际验证\n~~~',
    '<custom-block>\n## 实际验证\n</custom-block>',
    '> ## 实际验证\n> 引用中的说明',
    '- ## 实际验证\n  列表中的说明',
    '    ## 实际验证',
    '[ref]: /url\n<custom-block>\n## 实际验证\n</custom-block>'
  ]){
    const fake=body.replace(section,'\n\n'+prelude);
    assert(gate.prBodyErrors(fake).some(e=>e.includes('缺少可定位章节：实际验证')),prelude);
    const real=body.replace(section,'\n\n'+prelude+'\n\n'+section);
    assert.deepEqual(gate.prBodyErrors(real),[],prelude);
  }
  assert.deepEqual(gate.prBodyErrors(body.replace('## 实际验证','\n实际验证\n--------')),[]);
  assert.deepEqual(gate.prBodyErrors(body.replace('## 实际验证','### **实际验证**')),[]);
});

test('超出 PR 平台正文限制的输入必须拒绝',()=>{
  assert(gate.prBodyErrors('x'.repeat(65537)).some(e=>e.includes('长度上限')));
});

test('全部顶层标题层级均参与章节边界，标题文字不替代正文',()=>{
  const section='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const invalid=[
    '## 实际验证\n# 独立一级标题的文字很长但不属于先前章节内容',
    '## 实际验证\n# 独立一级标题\n此处正文属于新一级章节而非实际验证章节。',
    '## 实际验证\n### 子节标题很长但没有任何实际验证说明内容',
    '## 实际验证\n#### 四级标题的文字很长但没有任何验证正文',
    '## 实际验证\n### 下一级标题\n#### 更深层标题文字很多但仍然没有实际正文',
    '## 实际验证\n##### 第五级标题虽然很长但不能代替内容',
    '## 实际验证\n###### 第六级标题虽然很长但不能代替内容'
  ];
  for(const replacement of invalid){
    const errors=gate.prBodyErrors(body.replace(section,replacement));
    assert(errors.some(e=>e.includes('章节缺少有效内容：实际验证')),replacement+' '+JSON.stringify(errors));
  }
  const valid=body.replace(section,section+'\n# 下一个独立一级章节\n这段正文不属于上面的实际验证。');
  assert.deepEqual(gate.prBodyErrors(valid),[]);
});

test('二级章节包含后续子节正文，所有标题层级的文字均不计入说明',()=>{
  const original='## 背景与目标\n需要阻断不合规范的变更入口。';
  for(const replacement of [
    '## 背景与目标\n### 当前问题\n需要识别 Markdown 的真实章节层级和正文范围。',
    '## 背景与目标\n### 当前问题\n#### 具体场景\n需要识别 Markdown 的真实章节层级和正文范围。',
    '## 背景与目标\n#### 具体场景\n需要识别 Markdown 的真实章节层级和正文范围。',
    '## 背景与目标\n### 当前问题\n> # 引用示例的一级标题\n> Markdown 正文内容应保留在所属的父章节范围内。'
  ]){
    assert.deepEqual(gate.prBodyErrors(body.replace(original,replacement)),[],replacement);
  }
  const empty='## 背景与目标\n### 当前问题说明标题文字很长但未编写任何正文';
  const errors=gate.prBodyErrors(body.replace(original,empty));
  assert(errors.some(e=>e.includes('章节缺少有效内容：背景与目标')),JSON.stringify(errors));
});

test('三级必需章节支持四级子节正文，遇到二级章节正确截止',()=>{
  const original='## 实际验证\n完成了符合要求与违规请求的本地正反例测试。';
  const valid='### 实际验证\n#### 实际运行结果\n完成了符合要求与违规请求的本地正反例测试。';
  assert.deepEqual(gate.prBodyErrors(body.replace(original,valid)),[]);
  const empty='### 实际验证\n#### 只有标题，没有验证过程与结果说明';
  const errors=gate.prBodyErrors(body.replace(original,empty));
  assert(errors.some(e=>e.includes('章节缺少有效内容：实际验证')),JSON.stringify(errors));
});


test('HTML 字符实体解码后仍须遵守正文字符约束',()=>{
  const original='需要阻断不合规范的变更入口。';
  for(const entity of ['&#x202E;','&#8203;','&#xA0;','&nbsp;','&lrm;']){
    const sample=body.replace(original,original+entity);
    const errors=gate.prBodyErrors(sample);
    assert(errors.some(e=>e.includes('渲染后的正文')),entity+' '+JSON.stringify(errors));
  }
  const heading=body.replace('## 实际验证','## 实际验&#8203;证');
  assert(gate.prBodyErrors(heading).some(e=>e.includes('渲染后的正文')));
  const allowed=body.replace(original,original+'中文实体 &#x4E2D;、普通实体 &amp; 与普通空格。');
  assert.deepEqual(gate.prBodyErrors(allowed),[]);
  const literal=body.replace(original,original+' 示例代码 `&#8203;` 不会被解释为实体。');
  assert.deepEqual(gate.prBodyErrors(literal),[]);
});

test('嵌套的不同必需章节不得重复使用同一段正文',()=>{
  const old='## 背景与目标\\n需要阻断不合规范的变更入口。\\n## 变更范围与实现\\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。'.replaceAll('\\n','\n');
  const shared='## 背景与目标\\n### 修改范围与实现\\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。'.replaceAll('\\n','\n');
  const fail=gate.prBodyErrors(body.replace(old,shared));
  assert(fail.some(e=>e.includes('独立有效正文')),JSON.stringify(fail));
  const own='## 背景与目标\\n需要阻断不合规范的变更入口。\\n### 修改范围与实现\\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。'.replaceAll('\\n','\n');
  assert.deepEqual(gate.prBodyErrors(body.replace(old,own)),[]);
  const nested=body.replace('需要阻断不合规范的变更入口。','### 当前问题\\n需要阻断不合规范的变更入口。'.replaceAll('\\n','\n'));
  assert.deepEqual(gate.prBodyErrors(nested),[]);
  // 存在另一真实背景章节时，标题分配必须能够避开父子重复计数。
  const alternate=body.replace(old,
    ('## 背景与修改范围\\n### 修改范围与实现\\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。\\n## 背景与目标\\n需要阻断不合规范的变更入口。').replaceAll('\\n','\n'));
  assert.deepEqual(gate.prBodyErrors(alternate),[]);
});

test('原始 HTML、链接标题属性的实体解码不能绕过不可见字符检查',()=>{
  const original='需要阻断不合规范的变更入口。';
  for(const fragment of [
    '<span title="&#x202E;">说明</span>',
    '<span aria-label="&lrm;">说明</span>',
    '<img alt="&#8203;" src="/example.png">',
    '[结果说明](https://example.com "标题&#x202E;")'
  ]){
    const errors=gate.prBodyErrors(body.replace(original,original+fragment));
    assert(errors.some(e=>e.includes('HTML 或链接属性')),fragment+' '+JSON.stringify(errors));
  }
  const rawBlock=body.replace('## 实际验证','\\n<div>\\n&#8203;\\n</div>\\n\\n## 实际验证'.replaceAll('\\n','\n'));
  assert(gate.prBodyErrors(rawBlock).some(e=>e.includes('HTML 或链接属性')));
  const safeHtml=body.replace(original,original+'<span title="说明 &amp; 影响">实际说明</span>。');
  assert.deepEqual(gate.prBodyErrors(safeHtml),[]);
  const safeBlock=body.replace('## 实际验证','\\n<div>\\n这是正常的 HTML 文字。\\n</div>\\n\\n## 实际验证'.replaceAll('\\n','\n'));
  assert.deepEqual(gate.prBodyErrors(safeBlock),[]);
  const code=body.replace('## 实际验证','```html\n<span title="&#8203;">文字</span>\n```\n## 实际验证');
  assert.deepEqual(gate.prBodyErrors(code),[]);
});

test('HTML 注释与非解码区的字符实体不构成渲染后控制字符',()=>{
  const original='需要阻断不合规范的变更入口。';
  for(const fragment of [
    '<!-- &#8203; -->',
    '<!-- 数值实体 &#x202E;\n命名实体 &nbsp; -->',
    '<div><!-- &#8203; -->正常 HTML 文字</div>',
    '<span title="说明 &amp; 影响"><!-- &#8203; -->有效文本</span>',
    '<script>const example="&#8203;";</script>',
    '<style>.example::before { content:"&#8203;"; }</style>',
    '<![CDATA[&#8203;]]>'
  ]){
    const sample=body.replace(original,original+'\n\n'+fragment+'\n\n');
    assert.deepEqual(gate.prBodyErrors(sample),[],fragment);
  }
  const direct=body.replace(original,original+'<!-- &#8203; -->');
  assert.deepEqual(gate.prBodyErrors(direct),[]);
  const actual=body.replace(original,original+'<!-- \u200B -->');
  assert(gate.prBodyErrors(actual).some(e=>e.includes('PR 正文存在控制字符')));
});

test('HTML 属性与可解码文本不得借注释语法绕过字符限制',()=>{
  const original='需要阻断不合规范的变更入口。';
  for(const fragment of [
    '<span title="&#8203;">说明</span>',
    '<span title="<!-- &#8203; -->">说明</span>',
    '<span title="前缀 > &#x202E;">说明</span>',
    '<span title=&#8203;>说明</span>',
    '<div><!-- &#8203; --><span title="&#8203;">说明</span></div>',
    '<div>说明 &#8203;</div>',
    '<script title="&#8203;">const literal="&#8203;";</script>',
    '<textarea>&#8203;</textarea>'
  ]){
    const errors=gate.prBodyErrors(body.replace(original,original+'\n\n'+fragment+'\n\n'));
    assert(errors.some(e=>e.includes('HTML 或链接属性')),fragment+' '+JSON.stringify(errors));
  }
});

test('非必需子章节正文保留父章节归属且不复用已独立分配的子节',()=>{
  const before='## 背景与目标\n需要阻断不合规范的变更入口。\n## 变更范围与实现\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。';
  const valid='## 背景与目标\n### 当前问题\n需要阻断不合规范的变更入口。\n### 修改范围与实现\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。';
  assert.deepEqual(gate.prBodyErrors(body.replace(before,valid)),[]);
  const invalid='## 背景与目标\n### 当前问题\n只有简述。\n### 修改范围与实现\n添加提交主题和 PR 元数据验证，且验证源为准确的提交对象。';
  const errors=gate.prBodyErrors(body.replace(before,invalid));
  assert(errors.some(e=>e.includes('独立有效正文')),JSON.stringify(errors));
});
