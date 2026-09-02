import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ModelConfigs from "../pages/admin/ModelConfigs";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  };
});

const dashscopeConfig = {
  id: 1,
  name: "阿里云百炼",
  base_url: "https://dashscope.example/v1",
  api_key_masked: "sk-****1234",
  model_name: "qwen-plus",
  is_default: true,
  route_count: 10,
};

function route(id: number, modelName: string) {
  return {
    id,
    display_name: modelName,
    model_name: modelName,
    priority: id * 10,
    is_enabled: true,
    health_status: "healthy",
    failure_streak: 0,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("模型配置页面", () => {
  it("空列表显示可操作提示", async () => {
    (api.get as any).mockResolvedValue({ data: [] });
    render(<ModelConfigs />);

    expect(await screen.findByText(/暂无 API 接入/)).toBeInTheDocument();
    expect(screen.getByText(/密钥已加密保存/)).toBeInTheDocument();
  });

  it("编辑默认配置时保留默认开关", async () => {
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/routes") ? [] : [dashscopeConfig] }),
    );
    (api.put as any).mockResolvedValue({ data: dashscopeConfig });

    render(<ModelConfigs />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑接入" }));
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        "/admin/model-configs/1",
        expect.objectContaining({ is_default: true }),
      );
    });
  });

  it("默认直接展示所选 API 的十个模型，无需展开父表", async () => {
    const routes = Array.from({ length: 10 }, (_, index) => route(index + 1, `model-${index + 1}`));
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/routes") ? routes : [dashscopeConfig] }),
    );

    const { container } = render(<ModelConfigs />);

    expect(await screen.findByText("模型调用链 · 阿里云百炼（10）")).toBeInTheDocument();
    expect(screen.getAllByText("model-1")).toHaveLength(2);
    expect(screen.getAllByText("model-10")).toHaveLength(2);
    expect(container.querySelector(".ant-table-row-expand-icon")).toBeNull();
  });

  it("可以在表格内修改并保存模型优先级", async () => {
    const routes = [route(1, "qwen-plus")];
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/routes") ? routes : [dashscopeConfig] }),
    );
    (api.put as any).mockResolvedValue({ data: { ...routes[0], priority: 5 } });

    render(<ModelConfigs />);
    const input = await screen.findByLabelText("设置 qwen-plus 优先级");
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 qwen-plus 优先级" }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith("/admin/model-configs/1/routes/1", {
        display_name: "qwen-plus",
        model_name: "qwen-plus",
        priority: 5,
        is_enabled: true,
      });
    });
  });

  it("存在连通性错误时不再显示绿色正常", async () => {
    const failedRoute = {
      ...route(4, "ZHIPU/GLM-5.3"),
      last_error_code: "InvalidParameter",
      last_error_message: "The product is not activated",
    };
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/routes") ? [failedRoute] : [dashscopeConfig] }),
    );

    render(<ModelConfigs />);

    expect(await screen.findByText("检测失败")).toBeInTheDocument();
    expect(screen.queryByText("正常")).toBeNull();
  });

  it("可以切换查看另一套 API 的独立模型链", async () => {
    const deepseekConfig = {
      ...dashscopeConfig,
      id: 2,
      name: "DeepSeek",
      base_url: "https://deepseek.example/v1",
      model_name: "deepseek-chat",
      is_default: false,
      route_count: 2,
    };
    (api.get as any).mockImplementation((url: string) => {
      if (url === "/admin/model-configs/1/routes") return Promise.resolve({ data: [route(1, "qwen-plus")] });
      if (url === "/admin/model-configs/2/routes") {
        return Promise.resolve({ data: [route(2, "deepseek-pro"), route(3, "deepseek-flash")] });
      }
      return Promise.resolve({ data: [dashscopeConfig, deepseekConfig] });
    });

    render(<ModelConfigs />);
    await screen.findByText("模型调用链 · 阿里云百炼（1）");
    fireEvent.click(screen.getAllByRole("button", { name: "查看模型链" })[1]);

    expect(await screen.findByText("模型调用链 · DeepSeek（2）")).toBeInTheDocument();
    expect(screen.getAllByText("deepseek-pro")).toHaveLength(2);
    expect(screen.getAllByText("deepseek-flash")).toHaveLength(2);
  });

  it("空模型链只能通过明确操作导入阿里云模板", async () => {
    const emptyConfig = { ...dashscopeConfig, route_count: 0 };
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/routes") ? [] : [emptyConfig] }),
    );
    (api.post as any).mockResolvedValue({ data: [route(11, "qwen3.8-max")] });

    render(<ModelConfigs />);
    expect(await screen.findByText("当前模型链为空，请手工添加模型或导入模板")).toBeInTheDocument();
    expect(screen.queryByText(/将使用兜底模型/)).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "导入阿里云十模型模板" }));
    fireEvent.click(await screen.findByRole("button", { name: /确\s*定|OK/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        "/admin/model-configs/1/routes/presets/dashscope-current",
      );
    });
    expect(await screen.findAllByText("qwen3.8-max")).toHaveLength(2);
  });
});
