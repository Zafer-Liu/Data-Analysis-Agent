# TimesFM-3 本地接入

智析默认不下载权重、不安装 PyTorch，也不拉取 Docker 镜像。设置 → 本地服务统一管理连接；下方“下载或使用已有模型”提供 TimesFM 安装入口。

## 已有 API

添加服务并选择 TimesFM 协议，填写兼容服务的基础地址，如 `http://127.0.0.1:8787`，测试连接后点击“用于预测”。也可通过下方环境与预测设置使用合成数据试运行，再启用并保存。当前支持 `/health` 和 `/forecast` 协议，不是 OpenAI 聊天接口。

## Docker

这是本项目维护的适配器镜像，不是 Google 官方 Docker 镜像。先自行准备官方模型目录，包含 `config.json`、`model.safetensors` 和许可文件。

在本目录执行（示例需要用户主动操作；会安装/构建运行依赖，但不会下载模型权重）：

```powershell
$env:TIMESFM_MODEL_DIR = '你的模型绝对目录'
$env:TIMESFM_HOST_PORT = '8787'
docker compose up -d --build
```

GPU 需要主机、Docker 和 NVIDIA 运行时支持，并采用另一镜像标签，避免覆盖 CPU 构建：

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
```

在智析选择 Docker 接入，填写宿主机映射地址并测试。两者在同一 Docker 网络时使用服务名 `http://timesfm:8787`；智析容器访问宿主机服务时须选择可达地址，例如 Docker Desktop 的 `host.docker.internal`，并检查监听范围。`127.0.0.1` 始终指当前网络命名空间。

权重挂载只读，缺少权重会直接退出，不会在启动时补下载。CPU 为默认构建，CUDA 通过显式 GPU Compose 覆盖启用。可用 `TIMESFM_API_TOKEN` 给服务配置认证，密钥不要写入 Git。

## 已有模型目录 / 下载并配置

选择“下载模型”并填写保存位置，或选择“使用已有模型”并填写模型文件夹。已有模型默认将运行文件存到模型文件夹下的 `.sage-runtime`，可在高级设置中修改。已有环境需填写具备 `timesfm3`、`torch`、`numpy` 的 Python 可执行文件，然后点击“启动并使用”。外部 Python 环境不会被自动修改。

没有权重时，主动点击下载并配置，确认约 1.32 GB 权重和可能更大的运行依赖下载。应用会下载固定官方版本、验证 SHA-256、准备独立环境、分配端口、启动并试运行，成功后保存地址。默认不随应用自启，可独立开启；应用重启不会为自启补下载或安装依赖。

如果使用打包桌面版且尚无外部 Python 运行环境，请先准备 Python/模型环境或使用 API/Docker 接入；打包应用自身不是可用于创建 venv 的 Python 解释器。

应用托管实例，以及经可信本地安装流程登记了停止脚本的服务，可在本地服务列表中点击“停止模型”，结束后台进程并释放其显存；文件和连接配置会保留。未登记控制方式的外部 API 和自建容器由用户管理。下载、启动失败时查看所选运行目录 `logs/setup.log` / `logs/service.log`。下载中保留部分文件，再次主动操作可续传；权重与固定版本不符时拒绝启用。

## 对话中使用

导入本目录的 `example_history.csv` 后可提问：“使用 TimesFM 预测这三种商品未来 7 天，日期列 date，商品列 series_id，数值列 value，并画图。”

示例来自 [FreshRetailNet-50K](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K)，发布方 Dingdong-Inc，数据许可 CC-BY-4.0，固定版本 `c374e85975c82816c495acfa904cc55ab14a47d3`。选取 store_358 的三个商品历史并转换为长表，未包含未来标签。数值为来源数据缩放后的销量，不是实际件数或销售额。

也可明确使用分析入口：`run_analysis(analysis_name="Time_Series_TimesFM3", target_column="value", sql="SELECT date, series_id, value FROM ...", analysis_options={"time_column":"date","series_column":"series_id","horizon":7,"frequency":"D"})`。

Agent 当前支持目标序列联合预测，暂不接受协变量；独立服务协议支持协变量，为后续应用接入预留。回测选项 `backtest_steps` 默认 0；没有回测不能声称准确率。

模型来源：<https://huggingface.co/google/timesfm-3.0-pytorch>，revision `43046b85ec22d584a13f8098c2ed39c889e129c2`。权重限非商业、非生产研究；完整许可随模型下载。此功能未接入付费算力租赁或支付。
