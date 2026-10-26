# 论文精读 AI 接入

真实请求复用 deepseek_ai.py 的 HTTPS 传输与安全凭据读取；paper_lab.py 管理论文分段、精读、融合命题、缓存与核验。论文每步最多16000输出token，非思考模式；传输超时仍为120秒。书籍原有请求限制不变。

解析通过不代表语义正确；生产结果请核对原文。测试响应均为合成数据，未实际调用服务。

官方参数核对：https://api-docs.deepseek.com/api/create-chat-completion/

安装、费用、保存和测试边界以 README.md 为准。
