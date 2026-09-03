# DocFlow 发布文件与安全清单

## 可发布内容

- `app/`、`static/`、`tests/`、`scripts/` 与必要文档。
- `Dockerfile`、`compose.public-demo.yml`、`requirements.txt`、`.env.example`。
- 固定合成评测报告；报告必须明确它不是生产准确率。

## 不进入 Git 或发布包

- `.env`、模型密钥、访问密码及任何真实凭据。
- `data/`、`uploads/`、`*.db`、本地虚拟环境、缓存和用户原始材料。
- 服务器裸 IP、临时登录链接、真实客户数据和未脱敏截图。

## 发布前门禁

1. 运行完整单元/API/恢复测试、20 条固定评测、MCP 冒烟与依赖检查。
2. 设置 `DOCFLOW_DEMO_MODE=true`、随机用户名和强密码。
3. 保持 `DOCFLOW_BIND_ADDRESS=127.0.0.1`，通过 HTTPS 反向代理发布。
4. 确认 `/ready` 中 `public_demo_safe=true`，同时保留 `production_authentication=false` 的真实边界。
5. 以两个独立浏览器会话验证任务、Memory、审核、删除和导出不串用。
6. 只允许脱敏演示材料；审核前导出必须返回 409，审核后才可下载。
