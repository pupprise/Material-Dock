# 部署与运维

从仓库根目录安装依赖后，按以下示例初始化独立实例。

```powershell
.\.venv\Scripts\python.exe manage.py init --designer 样例1 --designer 样例2 --materials 样例3 --port 8765
.\.venv\Scripts\python.exe server.py
```

浏览器打开 `private/` 中对应账号邀请包里的链接，按说明输入初始化生成的密码。用 `/healthz` 检查连通，用 `/versionz` 检查版本。也可以运行 `start_dock.py --no-browser` 后台启动同一配置，进程编号记录在数据目录的 `server.pid`。

局域网使用 `manage.py init --interface 主机IPv4/前缀`。跨网段需在组织网络内配置路由，按需补充 `--peer-network`，并确认端口放行。使用 HTTPS 代理时，用 `--https-origin https://dock.example.com` 登记入口，保留用户访问域名的 Host 和 Origin。在代理层管理外网访问。公网可见页面每三分钟拉取一次工作台，隐藏时暂停。

### 数据、更新与回退

- 本地 `config.json` 指定数据目录。恢复需要私有配置、SQLite 文件和数据路径下的 `reference-photos/`。原图及缩略图由这一目录保存。
- 在停服窗口备份整个数据目录与配置，或者用 SQLite 在线备份 API 获取一致的数据库快照后同步对应原图。恢复先用测试副本验证。
- 升级前在独立端口试跑数据库副本。核对登录、权限、供应商、导入和导出后再切换生产服务。若需使用备份恢复，记录备份之后的写入。
- 内网接入、公网代理、正式账号和业务资料按各自所在环境维护。GitHub 仓库用于交付源码。
