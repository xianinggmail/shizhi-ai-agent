# 拾知：云端邀请体验版

此目录为独立部署源码，不包含项目所有者的实际论文、数据库或密钥。原电脑端项目不受影响。

## 部署步骤

1. 将本目录内容上传至GitHub仓库根目录（包含render.yaml）。
2. 登录 https://dashboard.render.com/ ，New → Blueprint，连接该仓库。
3. 在设置中填入自己的 DEEPSEEK_API_KEY，仅填平台密钥输入框，不写入代码。
4. 平台将创建付费 Web Service 和1GB持久磁盘，先核对显示的费用，再确认部署。
5. 等待 Live；复制平台提供的 HTTPS 地址。在 Environment 查看自动生成的 SHARE_ACCESS_CODE，将地址与访问码私下发给体验者。

访问码不是AI密钥；不要公开API Key。访问码持有人都可发起AI请求，费用由密钥所有者承担。修改SHARE_ACCESS_CODE后重新部署，可让旧登录失效。

## 体验范围

保持原有上传、学习卡片、原文核对、自评复习、论文精读、名词解释与论述训练。云端使用新的资料库；不自动迁移电脑上的资料。

这是一个受邀者共用的体验空间，不是用户资料相互隔离的多账号系统。所有受邀者都能查看、修改或删除共用资料。请只上传可共享测试材料，勿作为敏感个人学习资料库。

磁盘路径 /var/data/shizhi，运行时仅一个Gunicorn worker。不要改为多worker或多实例：异步任务与登录会话由单进程持有。重新部署会中断进行中的AI任务，已保存资料保留。进行中的付费请求不保证退款，不会自动付费重试。

## 运行配置

- DEEPSEEK_API_KEY：平台秘密环境变量。
- SHARE_ACCESS_CODE：至少20字符的私有邀请访问码，蓝图自动生成。
- STUDY_DATA_DIR：持久磁盘目录。
- PUBLIC_ORIGIN：自定义域名时填写完整HTTPS源地址；Render默认域名自动识别。
- PORT：由平台提供。

健康检查 /healthz 不调用AI、不返回资料。其余API均需访问码登录；写入另校验来源和页面令牌。浏览器登录cookie使用Secure、HttpOnly和SameSite=Strict。

本地测试使用模拟数据和内存WSGI请求。正式平台的构建、HTTPS和持久磁盘需要实际部署后验证；本目录不代表已经上线。
