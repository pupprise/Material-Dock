# 验收清单

在独立目录用 Python 3.12 安装依赖并执行：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check web/app.js
node --check web/supplier-ui.js
```

Node 只用于语法检查。应用运行使用 Python 和浏览器。测试使用独立 SQLite 临时库及程序生成的工作簿。

1. 启动三个演示入口。用对应模拟账号实际登录，核对两个设计师只能看到自己的数据，材料员可访问供应商库。
2. 在材料员侧下载供应商模板，手动录入一条并预览导入另一条。核对有效行、重名提示和电话文本，再以设计师账号按简称或主营物料搜索。
3. 在工作台组合搜索物料、项目和规格，确认详情图标可加载。材料员导出应与结果一致。
4. 用独立账号配置运行 `manage.py init`、`server.py` 和 `start_dock.py --no-browser`，读取健康、版本和登录结果。
5. 提交前查看 Git 暂存清单，确认其中没有配置、真实账号资料、密钥、数据库、个人邀请、运行日志、缓存和测试调试产物。
6. 推送后在新目录克隆，按主 README 重装依赖并重复上述步骤。比对本地和远端提交。
