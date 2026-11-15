// GPU 算力 domain — extracted verbatim from app_settings.js.
// Functions carry over unchanged; closure refs `uiState`/`draw` resolve via ctx.
import { runWebGpuVerification } from "../../core/webgpu-compute.js";
import { apiClient } from "../../core/api-client.js";
import { computeText, openComputeDataPanel, trainingConnectionReadiness } from "../../features/compute-data.js";
import { renderSwitch, _renderPanelHead } from "./shared.js";

const Vue = window.Vue;

export function createGpuDomain(ctx) {
  const { uiState, draw } = ctx;

  function renderGpu() {
    const gpu = uiState.gpuStatus;
    const webGpu = uiState.webGpu;
    const kind = gpu ? gpu.kind : "none";
    const hasNvidia = kind === "nvidia";
    const _hasDiscrete = kind === "nvidia" || kind === "discrete_wmi";
    const hasIntegrated = kind === "integrated";
    // 圆点：NVIDIA 独显=绿（online），集显=灰（offline），无=灰
    const dotClass = hasNvidia ? "online" : "offline";

    const gpuItems = [];
    if (gpu && gpu.gpus && gpu.gpus.length) {
      gpu.gpus.forEach(function (g) {
        if (hasNvidia) {
          // nvidia-smi 有详细显存/利用率
          gpuItems.push(Vue.h("div", { class: "gpu-item" }, [
            Vue.h("span", { class: "gpu-item-name" }, g.name),
            Vue.h("span", { class: "gpu-item-meta" },
              "显存 " + g.memory_used_mb + "/" + g.memory_total_mb + " MB · 利用率 " + g.utilization_pct + "%"),
          ]));
        } else {
          // WMI 只有型号（集显/discrete_wmi），无显存/利用率
          gpuItems.push(Vue.h("div", { class: "gpu-item" }, [
            Vue.h("span", { class: "gpu-item-name" }, g.name),
            Vue.h("span", { class: "gpu-item-meta" },
              g.kind === "integrated" ? "集显" : "独显"),
          ]));
        }
      });
    }
    // 无独显且有集显时不显示引导（已有型号列表）；纯无显卡时显示引导
    if (kind === "none" && !gpuItems.length) {
      gpuItems.push(Vue.h("div", { class: "gpu-hint" },
        "未检测到显卡。如需 GPU 推理与训练，可在本页「远程 GPU 连接」中配置 AutoDL 等远程服务器。"));
    } else if (hasIntegrated || (!hasNvidia && kind === "none" && gpuItems.length === 0)) {
      gpuItems.push(Vue.h("div", { class: "gpu-hint" },
        "本机无 NVIDIA 独显（不支持 CUDA）。如需 GPU 推理与训练，可在本页「远程 GPU 连接」中配置 AutoDL 等远程服务器。"));
    }

    const ollama = uiState.gpuOllama;
    const remoteConnections = (uiState.gpuConnections || []).map(function (connection) {
      const active = uiState.gpuConnectionStatus[connection.id] || {};
      const connected = !!active.connected;
      const trainingOnly = connection.purpose === "training";
      const inferenceReady = !trainingOnly && (active.inference_ready === true || connection.inference_ready === true);
      const readiness = trainingConnectionReadiness(connection);
      const models = uiState.gpuConnectionModels[connection.id] || [];
      return Vue.h("div", { class: "gpu-remote-item", key: connection.id }, [
        Vue.h("div", { class: "gpu-remote-copy" }, [
          Vue.h("strong", null, connection.name),
          Vue.h("span", null, connection.connection_type === "direct" ? connection.base_url : connection.username + "@" + connection.host + ":" + connection.port + (trainingOnly ? "" : " → " + connection.target_port)),
          Vue.h("span", { class: inferenceReady ? "gpu-remote-online" : "gpu-remote-offline" },
            inferenceReady ? computeText("inferenceReady", "Inference ready") : connected && !trainingOnly
              ? computeText("inferenceConnected", "Inference endpoint connected; readiness unavailable")
              : computeText("inferenceUnavailable", "Inference not ready")),
          Vue.h("span", { class: readiness === "ready" ? "gpu-remote-online" : "gpu-remote-offline" },
            computeText(`training.${readiness}`, "Training capabilities unavailable")),
          connection.training_runner?.families?.length ? Vue.h("span", { class: "gpu-remote-models" },
            "RPC v" + connection.training_runner.protocol_version + " · " + connection.training_runner.families.join(" / ")) : null,
          models.length ? Vue.h("span", { class: "gpu-remote-models" }, "模型：" + models.join("、")) : null,
          models.length ? Vue.h("div", { class: "gpu-remote-model-actions" }, models.map(model => Vue.h("button", {
            class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => registerGpuModel(connection.id, model),
          }, "注册 " + model)).concat(models.map(model => Vue.h("button", {
            class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => testGpuModel(connection.id, model),
          }, "测试 " + model)))) : null,
        ]),
        Vue.h("div", { class: "gpu-remote-actions" }, [
          !trainingOnly ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => connected ? disconnectGpuConnection(connection.id) : connectGpuConnection(connection.id),
          }, connected ? "断开" : "连接") : null,
          connected ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => discoverGpuModels(connection.id),
          }, "模型") : null,
          connection.connection_type === "ssh" ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => trustGpuHost(connection.id),
          }, computeText("verifyHost", "Verify SSH host")) : null,
          connection.connection_type === "ssh" ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy || connection.purpose === "inference",
            onClick: () => preflightTrainingRunner(connection.id),
          }, computeText("trainingPreflight", "Training preflight")) : null,
          connection.connection_type === "ssh" ? Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button",
            onClick: () => openComputeDataPanel("training", { connectionId: connection.id }),
          }, computeText("openTraining", "Open training panel")) : null,
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: () => deleteGpuConnection(connection.id),
          }, "删除"),
        ]),
      ]);
    });
    const form = uiState.gpuConnectionForm;
    return Vue.h("section", { class: "app-settings-panel" }, [
      _renderPanelHead("GPU 算力", "检测服务器与浏览器 GPU 状态；WebGPU 可执行浏览器本地数值任务。", [
        Vue.h("button", {
          class: "btn-sm btn-sm-ghost",
          type: "button",
          disabled: uiState.gpuRefreshing || uiState.gpuBusy || uiState.gpuLoading,
          onClick: refreshGpuStatus,
        }, uiState.gpuRefreshing ? "检测中…" : "重新检测"),
      ]),
      // 开关行
      Vue.h("label", { class: "app-setting-row" }, [
        Vue.h("span", { class: "app-setting-copy" }, [
          Vue.h("strong", null, "启用 GPU 算力"),
          Vue.h("span", null, "开启后本地 GPU 优先用于推理与训练；关闭则使用云端 API 与 CPU。"),
        ]),
        renderSwitch(uiState.gpuEnabled, setGpuEnabled),
      ]),
      // 检测状态行
      Vue.h("div", { class: "gpu-status-row" }, [
        Vue.h("span", { class: "gpu-dot " + dotClass }),
        Vue.h("span", { class: "gpu-status-text" },
          uiState.gpuLoading ? "检测中…" : (gpu && gpu.message) || "尚未检测"),
      ]),
      // GPU 型号列表 + 引导
      ...gpuItems,
      // 浏览器 WebGPU 独立于服务端 CUDA/WMI 检测：云端部署时仍可检测访问者设备。
      Vue.h("div", { class: "gpu-browser-section" }, [
        Vue.h("div", { class: "gpu-browser-head" }, [
          Vue.h("div", { class: "gpu-browser-title" }, "浏览器 WebGPU"),
          Vue.h("button", {
            class: "btn-sm btn-sm-ghost", type: "button",
            disabled: uiState.webGpuLoading || uiState.webGpuBenchmarking,
            onClick: runWebGpuBenchmark,
          }, uiState.webGpuBenchmarking ? "计算中…" : "运行 GPU 计算"),
        ]),
        Vue.h("div", { class: "gpu-status-row" }, [
          Vue.h("span", { class: "gpu-dot " + (webGpu && webGpu.available ? "online" : "offline") }),
          Vue.h("span", { class: "gpu-status-text" },
            uiState.webGpuLoading ? "检测中…" : (webGpu && webGpu.message) || "尚未检测"),
        ]),
        webGpu && webGpu.available ? Vue.h("div", { class: "gpu-item" }, [
          Vue.h("span", { class: "gpu-item-name" }, webGpu.name),
          Vue.h("span", { class: "gpu-item-meta" }, "浏览器 GPU 加速可用"),
        ]) : null,
        Vue.h("div", { class: "gpu-browser-note" },
          "WebGPU 会在当前浏览器内执行本地数值任务；它不会让云端服务获得本机 CUDA 或用于后端训练。"),
      ]),
      // Ollama 状态
      ollama ? Vue.h("div", { class: "gpu-ollama-row" }, [
        Vue.h("span", { class: "gpu-dot " + (ollama.online ? "online" : "offline") }),
        ollama.online
          ? "本地 Ollama：在线 · " + (ollama.models || []).length + " 个模型"
          : "本地 Ollama：未运行（可安装 Ollama 后在 LLM 模型 tab 填写模型名）",
      ]) : null,
      Vue.h("div", { class: "gpu-remote-section" }, [
        Vue.h("div", { class: "gpu-remote-head" }, [
          Vue.h("div", null, [Vue.h("strong", null, computeText("remoteGpuConnections", "Remote GPU connections")),
            Vue.h("span", null, computeText("readinessHint", "Inference and training readiness are checked separately. Approve data transfer in the training panel."))]),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: () => openComputeDataPanel("training") }, computeText("openCompute", "Data and compute")),
          Vue.h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: loadGpuConnections,
          }, "刷新"),
        ]),
        ...(remoteConnections.length ? remoteConnections : [Vue.h("div", { class: "gpu-hint" }, "尚未添加远程连接。连接密码仅保存到系统凭据库，不会写入配置文件。")]),
        Vue.h("div", { class: "gpu-remote-form" }, [
          Vue.h("select", { value: form.connectionType, "aria-label": computeText("connectionType", "Connection type"), onChange: e => { form.connectionType = e.target.value; } }, [
            Vue.h("option", { value: "ssh" }, computeText("sshConnection", "SSH connection")),
            Vue.h("option", { value: "direct" }, computeText("httpsEndpoint", "Public HTTPS endpoint")),
          ]),
          form.connectionType === "ssh" ? Vue.h("select", { value: form.purpose || "inference", "aria-label": computeText("connectionPurpose", "Connection purpose"), onChange: e => { form.purpose = e.target.value; } }, [
            Vue.h("option", { value: "inference" }, computeText("inferenceOnly", "Inference only")),
            Vue.h("option", { value: "training" }, computeText("trainingOnly", "Training only")),
            Vue.h("option", { value: "both" }, computeText("inferenceAndTraining", "Inference and training")),
          ]) : null,
          Vue.h("input", { value: form.name, placeholder: "连接名称", "aria-label": computeText("name", "Connection name"), onInput: e => { form.name = e.target.value; } }),
          form.connectionType === "ssh" ? Vue.h("input", { value: form.port || "22", type: "number", min: 1, max: 65535, "aria-label": computeText("sshPort", "SSH port"), placeholder: "22", onInput: e => { form.port = e.target.value; } }) : null,
          form.connectionType === "direct" ? Vue.h("input", { value: form.baseUrl, "aria-label": computeText("httpsEndpoint", "Public HTTPS endpoint"), placeholder: "https://gpu.example.com", onInput: e => { form.baseUrl = e.target.value; } }) : Vue.h("input", { value: form.host, "aria-label": computeText("sshHost", "SSH host"), placeholder: computeText("sshHost", "SSH host"), onInput: e => { form.host = e.target.value; } }),
          form.connectionType === "ssh" ? Vue.h("input", { value: form.username, "aria-label": computeText("sshUsername", "SSH username"), placeholder: computeText("sshUsername", "SSH username"), onInput: e => { form.username = e.target.value; } }) : null,
          form.connectionType === "ssh" && form.purpose !== "training" ? Vue.h("input", { value: form.targetPort, placeholder: "远端服务端口（如 8000）", "aria-label": computeText("inferencePort", "Inference service port"), inputmode: "numeric", onInput: e => { form.targetPort = e.target.value; } }) : null,
          form.connectionType === "ssh" ? Vue.h("select", { value: form.authMethod, "aria-label": computeText("auth", "Authentication"), onChange: e => { form.authMethod = e.target.value; } }, [
            Vue.h("option", { value: "agent" }, computeText("sshAgent", "SSH Agent / default key")),
            Vue.h("option", { value: "password" }, computeText("vaultPassword", "Password (system credential vault)")),
          ]) : null,
          form.connectionType === "ssh" && form.authMethod === "password" ? Vue.h("input", { value: form.password, type: "password", autocomplete: "new-password", "aria-label": computeText("sshPassword", "SSH password"), placeholder: computeText("sshPassword", "SSH password"), onInput: e => { form.password = e.target.value; } }) : null,
          Vue.h("button", { class: "btn-sm btn-sm-primary", type: "button", disabled: uiState.gpuRemoteBusy,
            onClick: createGpuConnection,
          }, "添加安全连接"),
        ]),
        uiState.gpuRemoteMessage ? Vue.h("div", { class: "gpu-remote-message" }, uiState.gpuRemoteMessage) : null,
      ]),
    ]);
  }

  // ── GPU 算力（G1/G2）──────────────────────────────────────────
  async function loadGpuStatus() {
    if (!uiState) return;
    if (uiState.gpuLoading) return;
    uiState.gpuLoading = true;
    draw();
    try {
      const resp = await fetch("/api/gpu/status");
      if (!resp.ok) {
        uiState.gpuStatus = {
          kind: "none",
          gpus: [],
          message: resp.status === 404
            ? "当前为云端部署；服务器 GPU 检测不可用，请查看下方浏览器 WebGPU 状态"
            : "服务器 GPU 状态获取失败",
        };
      } else {
        const data = await resp.json();
        uiState.gpuStatus = data.gpu || null;
        uiState.gpuOllama = data.ollama || null;
        uiState.gpuEnabled = !!data.enabled;
      }
    } catch (_err) {
      uiState.gpuStatus = { kind: "none", gpus: [], message: "GPU 状态获取失败" };
    }
    uiState.gpuLoading = false;
    draw();
  }

  async function refreshGpuStatus() {
    if (!uiState || uiState.gpuRefreshing || uiState.gpuBusy) return;
    uiState.gpuRefreshing = true;
    draw();
    try {
      const resp = await fetch("/api/gpu/status");
      if (!resp.ok) throw new Error("GPU status request failed");
      const data = await resp.json();
      uiState.gpuStatus = data.gpu || null;
      uiState.gpuOllama = data.ollama || null;
      uiState.gpuEnabled = !!data.enabled;
    } catch (_err) {
      /* 保持旧状态 */
    }
    await detectWebGpu();
    uiState.gpuRefreshing = false;
    draw();
  }

  async function detectWebGpu() {
    if (!uiState || uiState.webGpuLoading) return;
    uiState.webGpuLoading = true;
    draw();
    try {
      uiState.webGpu = await runWebGpuVerification();
    } finally {
      uiState.webGpuLoading = false;
      draw();
    }
  }

  async function runWebGpuBenchmark() {
    if (!uiState || uiState.webGpuLoading || uiState.webGpuBenchmarking) return;
    uiState.webGpuBenchmarking = true;
    draw();
    try {
      uiState.webGpu = await runWebGpuVerification(1_000_000);
    } finally {
      uiState.webGpuBenchmarking = false;
      draw();
    }
  }

  async function setGpuEnabled(enabled) {
    if (!uiState || uiState.gpuBusy) return;
    uiState.gpuBusy = true;
    draw();
    try {
      await fetch("/api/gpu/enabled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      uiState.gpuEnabled = !!enabled;
    } catch (_err) {
      /* 保持原状态 */
    }
    uiState.gpuBusy = false;
    draw();
  }

  async function _gpuRequest(url, options = {}) {
    return apiClient.request(url, { ...options, responseType: "json", requireOk: true });
  }

  async function loadGpuConnections() {
    if (!uiState) return;
    try {
      const data = await _gpuRequest("/api/gpu/connections");
      uiState.gpuConnections = data.connections || [];
      await Promise.all(uiState.gpuConnections.map(async connection => {
        const status = await _gpuRequest("/api/gpu/connections/" + connection.id + "/status");
        uiState.gpuConnectionStatus[connection.id] = status;
      }));
      uiState.gpuRemoteMessage = "";
    } catch (err) { uiState.gpuRemoteMessage = err.message || "读取连接失败"; }
    draw();
  }

  async function createGpuConnection() {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      const form = uiState.gpuConnectionForm;
      await _gpuRequest("/api/gpu/connections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        connection_type: form.connectionType, purpose: form.connectionType === "ssh" ? form.purpose || "inference" : "inference",
        name: form.name, base_url: form.baseUrl, host: form.host, port: Number(form.port || 22), username: form.username,
        ...(form.purpose !== "training" ? { target_port: form.targetPort } : {}), auth_method: form.authMethod, password: form.password,
      }) });
      uiState.gpuConnectionForm = { connectionType: "ssh", name: "", host: "", username: "", targetPort: "8000", baseUrl: "", authMethod: "agent", password: "" };
      await loadGpuConnections();
    } catch (err) { uiState.gpuRemoteMessage = err.message || "添加连接失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }

  async function connectGpuConnection(id) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      try { await _gpuRequest("/api/gpu/connections/" + id + "/connect", { method: "POST" }); }
      catch (err) {
        if (!String(err.message).includes("主机尚未确认")) throw err;
        const inspected = await _gpuRequest("/api/gpu/connections/" + id + "/host-key", { method: "POST" });
        const key = inspected.host_key;
        const trusted = await window.BAA.ui?.confirm?.({ title: "确认 SSH 主机指纹", message: "请核对 SSH 主机指纹后确认：\n" + key.fingerprint, confirmText: "确认", cancelText: "取消" });
        if (!trusted) throw new Error("未确认 SSH 主机指纹");
        await _gpuRequest("/api/gpu/connections/" + id + "/trust-host-key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key_type: key.type, key_base64: key.base64 }) });
        await _gpuRequest("/api/gpu/connections/" + id + "/connect", { method: "POST" });
      }
      await loadGpuConnections();
    } catch (err) { uiState.gpuRemoteMessage = err.message || "连接失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }

  async function disconnectGpuConnection(id) { await _gpuRequest("/api/gpu/connections/" + id + "/disconnect", { method: "POST" }); await loadGpuConnections(); }
  async function deleteGpuConnection(id) { const ok = await window.BAA.ui?.confirm?.({ title: "删除远程连接", message: "删除此远程连接？", danger: true }); if (ok) { await _gpuRequest("/api/gpu/connections/" + id, { method: "DELETE" }); await loadGpuConnections(); } }
  async function discoverGpuModels(id) {
    try { const data = await _gpuRequest("/api/gpu/connections/" + id + "/models"); uiState.gpuConnectionModels[id] = data.models || []; }
    catch (err) { uiState.gpuRemoteMessage = err.message || "模型发现失败"; }
    draw();
  }
  async function registerGpuModel(id, model) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      const data = await _gpuRequest("/api/gpu/connections/" + id + "/models/register", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
      });
      uiState.gpuRemoteMessage = data.message || "模型已注册";
    } catch (err) { uiState.gpuRemoteMessage = err.message || "模型注册失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }
  async function testGpuModel(id, model) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    uiState.gpuRemoteMessage = "正在验证 " + model + " 的实际推理…";
    draw();
    try {
      const data = await _gpuRequest("/api/gpu/connections/" + id + "/models/test", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
      });
      uiState.gpuRemoteMessage = data.message + (data.reply ? "：" + data.reply : "");
    } catch (err) { uiState.gpuRemoteMessage = err.message || "模型推理测试失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }
  async function trustGpuHost(id) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    draw();
    try {
      const inspected = await _gpuRequest("/api/gpu/connections/" + encodeURIComponent(id) + "/host-key", { method: "POST" });
      const key = inspected.host_key;
      if (!key?.fingerprint || !key.type || !key.base64) throw new Error(computeText("hostKeyUnavailable", "The server did not return a complete SSH host fingerprint."));
      const connection = (uiState.gpuConnections || []).find(item => item.id === id);
      const approved = await window.BAA.ui?.confirm?.({
        title: computeText("verifyHost", "Verify SSH host"),
        message: `${connection?.host || id}\n${key.fingerprint}\n${computeText("hostKeyHint", "Compare this fingerprint with your server administrator before trusting this host.")}`,
        confirmText: computeText("trustHost", "Trust this host"), cancelText: computeText("cancel", "Cancel"),
      });
      if (!approved) return;
      await _gpuRequest("/api/gpu/connections/" + encodeURIComponent(id) + "/trust-host-key", { method: "POST", body: { key_type: key.type, key_base64: key.base64 } });
      uiState.gpuRemoteMessage = computeText("hostTrusted", "SSH host fingerprint saved. You can run training preflight.");
    } catch (error) { uiState.gpuRemoteMessage = error.message || computeText("hostKeyUnavailable", "The SSH host fingerprint is unavailable."); }
    finally { uiState.gpuRemoteBusy = false; draw(); }
  }

  async function preflightTrainingRunner(id) {
    if (!uiState || uiState.gpuRemoteBusy) return;
    uiState.gpuRemoteBusy = true;
    try {
      const data = await _gpuRequest("/api/gpu/connections/" + id + "/training/preflight", { method: "POST" });
      const readiness = trainingConnectionReadiness({ training_runner: data.training_runner });
      await loadGpuConnections();
      uiState.gpuRemoteMessage = readiness === "ready"
        ? computeText("preflightReady", "Training preflight complete. Review data and budgets in the training panel.")
        : computeText("preflightUnavailable", "Training capabilities remain unavailable.");
    } catch (err) { uiState.gpuRemoteMessage = err.message || "远程训练器预检失败"; }
    uiState.gpuRemoteBusy = false;
    draw();
  }

  return {
    renderGpu,
    loadGpuStatus,
    refreshGpuStatus,
    detectWebGpu,
    runWebGpuBenchmark,
    setGpuEnabled,
    loadGpuConnections,
    createGpuConnection,
    connectGpuConnection,
    disconnectGpuConnection,
    deleteGpuConnection,
    discoverGpuModels,
    registerGpuModel,
    testGpuModel,
    preflightTrainingRunner,
    trustGpuHost,
  };
}
