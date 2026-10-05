# XmlEntityResourceBoundary 0.1.1

作者：dhtfish98。本项目独立实现一个范围有限的 XML 解析入口，使用 Python 标准库的 Expat 绑定处理语法，并在自己的入口上限制输入字节数、内部实体的声明数量/替换体大小/递归深度/预计算展开大小，以及保留在解析树中的名称、文本和属性值的总字节数。外部 DTD、外部实体和参数实体默认拒绝。它返回元素、属性和文本组成的小型树；不是通用 XML 处理器。版本与署名更正范围见 [版本记录](VERSION_STATUS.md)。

实现位于 [src/xml_entity_resource_boundary](../src/xml_entity_resource_boundary)，测试位于 [tests](../tests)，许可证与说明集中在「项目文档」。从仓库根目录运行验证后，测试日志、分发包、安装环境和实验收据统一写入 `Build`；这些生成内容不会提交。`src` 和 `tests` 仅保存源码。

## 使用与边界

```python
from xml_entity_resource_boundary import XmlPolicy, parse_xml

result = parse_xml(
    b'<root><item id="7">safe &amp; sound</item></root>',
    policy=XmlPolicy(max_visible_bytes=4096),
)
assert result.root.name == "root"
assert result.root.text_content() == "safe & sound"
```

输入必须是完整的 XML `bytes`。默认原始输入上限 64 KiB、保留的名称/文本/属性值总计 64 KiB、单个内部实体预计算展开 256 字节、最多 16 个实体声明、实体嵌套深度 12、元素数量 4096、元素深度 64；其他限制在 `XmlPolicy` 中明确列出。内嵌一般实体在预算内可用；未声明的参数实体引用同样拒绝，重复同名实体声明即使未超数量预算也保守拒绝。外部文件/网络实体和外部 DTD 被拒绝。正常输入保留元素层次、属性和文本内容，不保留注释、处理指令、原始实体写法或字节级格式。`XmlResult` 中的文本与属性可能敏感，调用方应按敏感数据处理。

异常按超预算、外部资源、未支持结构和语法错误区分。语法错误只给行/列，不回显 XML 内容。DTD 先由不展开内部一般实体的 Expat 默认回调读取，再检查参数实体引用和真实实体声明数量；正式解析收到的声明回调数须与预检一致，以拒绝 Expat 跳过的重复声明。可见字节预算在正式解析回调中执行，单实体预算在 DTD 结束后、内容使用前预计算；这些检查并非解析器内部瞬时内存使用的形式化上限。调用方仍应把不可信 XML 放在合适的进程与资源限额内。

## 自有实验与验证

`tests/run_local_experiment.py` 在 Build 下创建一次性合成标记文件。**故意弱化的自有基线**只允许读取这个确切的本机 `file://` URI，并把外部实体文本展开；正式入口面对同一输入在声明阶段拒绝，文件读取探针为零。另一个嵌套实体样例中，弱基线展开为 256 字节，正式入口在 128 字节单实体预算下拒绝。正常 XML 仍保留根、子元素、属性和文本。收据只记录散列、长度、布尔值、异常类别和本地实验路径，不保存合成标记值。

完整本地验收入口：

```text
python -m pip install uv==0.11.4
python .github/scripts/validate.py
```

验证入口测试源码、构建 sdist/wheel、在 `Build` 中创建隔离环境并安装 wheel，再从源码目录外执行安装版测试和实验。每个实际命令的退出码、日志哈希以及文件与包的精确匹配写入本地 `Build/validation.json`。GitHub Actions 在 Python 3.11 和 3.14 上运行同一个入口。

## 来源与许可

问题范围参考 [libexpat 固定快照 `ea81746aa19794418825657dc82121f2a6779143` 中的实体处理](https://github.com/libexpat/libexpat/blob/ea81746aa19794418825657dc82121f2a6779143/expat/lib/xmlparse.c)及[声明角色识别](https://github.com/libexpat/libexpat/blob/ea81746aa19794418825657dc82121f2a6779143/expat/lib/xmlrole.c)。快照的上游 [COPYING](https://github.com/libexpat/libexpat/blob/ea81746aa19794418825657dc82121f2a6779143/COPYING) 为 MIT。该 SHA 的[提交本身](https://github.com/libexpat/libexpat/commit/ea81746aa19794418825657dc82121f2a6779143)仅更新 Fil-C CI 工具链版本，并非实体安全漏洞修复。本项目没有复制、移植或改署名 Expat 源码；运行时使用 Python 自带的 Expat 绑定。新写项目代码采用本项目 [MIT 许可证](LICENSE)，构建时复制进 `Build` 暂存目录以随包分发。

本实验不代表上游 Expat 存在上述弱化行为，也不证明 CVP 资格、真实授权任务受到模型防护影响或外部部署有效性。本地验收分别使用 Python 3.11.15 / Expat 2.6.3 和 Python 3.14.6 / Expat 2.7.4；其他运行时版本仍需各自验收。XML 编码与实体兼容、DTD 的完整语义、XInclude、Schema、XPath、签名安全、跨进程资源配额和非本入口的解析路径均不在此证明范围内。
