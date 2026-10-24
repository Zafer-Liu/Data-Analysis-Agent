// Browser-local WebGPU capability and compute helpers.
//
// These helpers deliberately keep data in the browser: they are for workloads
// that the web client explicitly opts into, never a replacement for server-side
// CUDA/PyTorch training.

const WORKGROUP_SIZE = 256;
const MAX_VECTOR_LENGTH = 4_000_000;
const INITIALIZATION_TIMEOUT_MS = 8_000;
const COMPUTE_TIMEOUT_MS = 12_000;

const VECTOR_ADD_SHADER = /* wgsl */ `
struct Parameters {
  length: u32,
}

@group(0) @binding(0) var<storage, read> left: array<f32>;
@group(0) @binding(1) var<storage, read> right: array<f32>;
@group(0) @binding(2) var<storage, read_write> output: array<f32>;
@group(0) @binding(3) var<uniform> params: Parameters;

@compute @workgroup_size(${WORKGROUP_SIZE})
fn vector_add(@builtin(global_invocation_id) invocation: vec3<u32>) {
  let index = invocation.x;
  if (index >= params.length) {
    return;
  }
  output[index] = left[index] + right[index];
}
`;

function unavailable(message) {
  return { available: false, message };
}

class WebGpuComputeError extends Error {}

function withTimeout(promise, timeoutMs, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = globalThis.setTimeout(() => reject(new WebGpuComputeError(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => globalThis.clearTimeout(timeoutId));
}

function browserGpu() {
  return globalThis.navigator?.gpu || null;
}

async function adapterDescription(adapter) {
  try {
    let info = null;
    if (adapter.info) info = await adapter.info;
    else if (typeof adapter.requestAdapterInfo === "function")
      info = await adapter.requestAdapterInfo();
    return info?.description || "浏览器未公开显卡型号";
  } catch (_error) {
    // Adapter details may be withheld by browser privacy policy.
    return "浏览器未公开显卡型号";
  }
}

async function acquireDevice() {
  const gpu = browserGpu();
  if (!gpu) {
    return unavailable(
      globalThis.isSecureContext === false
        ? "此页面不是安全上下文，WebGPU 已被浏览器禁用"
        : "当前浏览器不支持 WebGPU；请使用最新版 Chrome 或 Edge",
    );
  }
  const adapter = await withTimeout(
    gpu.requestAdapter({ powerPreference: "high-performance" }),
    INITIALIZATION_TIMEOUT_MS,
    "等待 WebGPU 图形适配器超时",
  );
  if (!adapter) return unavailable("未获取到可用的 WebGPU 图形适配器");
  return {
    available: true,
    adapter,
    device: await withTimeout(
      adapter.requestDevice(),
      INITIALIZATION_TIMEOUT_MS,
      "创建 WebGPU 计算设备超时",
    ),
  };
}

function destroyBuffers(buffers) {
  buffers.forEach((buffer) => buffer?.destroy?.());
}

async function executeVectorAdd(device, left, right) {
  if (
    !(left instanceof Float32Array) ||
    !(right instanceof Float32Array) ||
    left.length !== right.length
  ) {
    throw new TypeError("WebGPU 向量计算需要两个长度相同的 Float32Array");
  }
  if (!left.length || left.length > MAX_VECTOR_LENGTH) {
    throw new RangeError(`WebGPU 向量长度须在 1 至 ${MAX_VECTOR_LENGTH} 之间`);
  }
  const usage = globalThis.GPUBufferUsage;
  const mapMode = globalThis.GPUMapMode;
  if (!usage || !mapMode) throw new Error("当前浏览器的 WebGPU 缓冲区 API 不完整");

  const byteLength = left.byteLength;
  const inputLeft = device.createBuffer({
    size: byteLength,
    usage: usage.STORAGE | usage.COPY_DST,
  });
  const inputRight = device.createBuffer({
    size: byteLength,
    usage: usage.STORAGE | usage.COPY_DST,
  });
  const output = device.createBuffer({ size: byteLength, usage: usage.STORAGE | usage.COPY_SRC });
  const parameters = device.createBuffer({ size: 4, usage: usage.UNIFORM | usage.COPY_DST });
  const readable = device.createBuffer({
    size: byteLength,
    usage: usage.COPY_DST | usage.MAP_READ,
  });
  const buffers = [inputLeft, inputRight, output, parameters, readable];

  try {
    device.queue.writeBuffer(inputLeft, 0, left);
    device.queue.writeBuffer(inputRight, 0, right);
    device.queue.writeBuffer(parameters, 0, new Uint32Array([left.length]));
    const pipeline = device.createComputePipeline({
      layout: "auto",
      compute: {
        module: device.createShaderModule({ code: VECTOR_ADD_SHADER }),
        entryPoint: "vector_add",
      },
    });
    const bindGroup = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: { buffer: inputLeft } },
        { binding: 1, resource: { buffer: inputRight } },
        { binding: 2, resource: { buffer: output } },
        { binding: 3, resource: { buffer: parameters } },
      ],
    });
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass();
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(Math.ceil(left.length / WORKGROUP_SIZE));
    pass.end();
    encoder.copyBufferToBuffer(output, 0, readable, 0, byteLength);
    device.queue.submit([encoder.finish()]);
    await withTimeout(
      readable.mapAsync(mapMode.READ),
      COMPUTE_TIMEOUT_MS,
      "等待 WebGPU 计算结果超时",
    );
    const result = new Float32Array(readable.getMappedRange().slice(0));
    readable.unmap();
    return result;
  } finally {
    destroyBuffers(buffers);
  }
}

/**
 * Run a bounded vector-add workload on the visitor's local GPU and verify its
 * result. This is both a capability probe and the first browser-local numeric
 * compute primitive for future client-side analysis tasks.
 */
export async function runWebGpuVerification(elementCount = 4096) {
  const count = Math.max(1, Math.min(Math.floor(Number(elementCount) || 0), MAX_VECTOR_LENGTH));
  let device = null;
  try {
    const acquired = await acquireDevice();
    if (!acquired.available) return acquired;
    const { adapter } = acquired;
    device = acquired.device;
    const left = new Float32Array(count);
    const right = new Float32Array(count);
    for (let index = 0; index < count; index += 1) {
      left[index] = index * 0.5;
      right[index] = 1.25 - index * 0.25;
    }
    const startedAt = performance.now();
    const result = await executeVectorAdd(device, left, right);
    const durationMs = performance.now() - startedAt;
    const sampleIndexes = [0, Math.floor(count / 2), count - 1];
    const valid = sampleIndexes.every(
      (index) => Math.abs(result[index] - (left[index] + right[index])) < 0.0001,
    );
    if (!valid) throw new Error("WebGPU 计算结果校验失败");

    const name = await adapterDescription(adapter);
    return {
      available: true,
      name,
      elementCount: count,
      durationMs,
      message:
        name === "浏览器未公开显卡型号"
          ? `WebGPU 已实际完成 ${count.toLocaleString()} 个数值计算`
          : `WebGPU 已实际完成 ${count.toLocaleString()} 个数值计算：${name}`,
    };
  } catch (error) {
    return unavailable(
      error instanceof WebGpuComputeError
        ? error.message
        : "WebGPU 初始化成功，但本机图形适配器无法完成数值计算",
    );
  } finally {
    device?.destroy?.();
  }
}
