import { useState } from "react";
import { Avatar, Dropdown, Form, Input, Modal, Space, message } from "antd";
import type { MenuProps } from "antd";
import {
  DesktopOutlined,
  DownOutlined,
  MoonOutlined,
  SunOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuthStore } from "../store/auth";
import { useThemeStore } from "../store/theme";
import type { ThemeMode } from "../theme/theme";

interface UserMenuProps {
  dark?: boolean;
  showAdminEntry?: boolean;
  showThemeSettings?: boolean;
}

const themeOptions: Array<{ key: ThemeMode; label: string; icon: React.ReactNode }> = [
  { key: "light", label: "浅色", icon: <SunOutlined /> },
  { key: "dark", label: "深色", icon: <MoonOutlined /> },
  { key: "system", label: "系统跟随", icon: <DesktopOutlined /> },
];

export default function UserMenu({
  dark = false,
  showAdminEntry = false,
  showThemeSettings = false,
}: UserMenuProps) {
  const user = useAuthStore((state) => state.user);
  const clearAuth = useAuthStore((state) => state.logout);
  const themeMode = useThemeStore((state) => state.mode);
  const setThemeMode = useThemeStore((state) => state.setMode);
  const navigate = useNavigate();
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordForm] = Form.useForm();

  const changePassword = async () => {
    const { old_password, new_password } = await passwordForm.validateFields();
    try {
      await api.post("/auth/change-password", { old_password, new_password });
      message.success("密码修改成功，请重新登录");
      setPasswordOpen(false);
      passwordForm.resetFields();
      clearAuth();
      navigate("/login");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "修改失败");
    }
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      // JWT 登出以清理本地凭据为准，后端不可用时也必须允许用户退出。
    } finally {
      clearAuth();
      navigate("/login");
    }
  };

  const currentTheme = themeOptions.find((option) => option.key === themeMode)!;
  const menuItems: MenuProps["items"] = [
    ...(showThemeSettings
      ? [
          {
            key: "theme",
            label: `主题：${currentTheme.label}`,
            icon: currentTheme.icon,
            children: themeOptions.map((option) => ({
              key: `theme:${option.key}`,
              label: option.label,
              icon: option.icon,
              onClick: () => setThemeMode(option.key),
            })),
          },
        ]
      : []),
    {
      key: "password",
      label: "修改密码",
      onClick: () => {
        passwordForm.resetFields();
        setPasswordOpen(true);
      },
    },
    ...(showAdminEntry && user?.role === "admin"
      ? [
          {
            key: "admin",
            label: "进入管理后台",
            onClick: () => navigate("/admin"),
          },
        ]
      : []),
    {
      key: "logout",
      label: "退出登录",
      onClick: logout,
    },
  ];

  return (
    <>
      <Dropdown
        menu={{
          items: menuItems,
          selectable: showThemeSettings,
          selectedKeys: showThemeSettings ? [`theme:${themeMode}`] : [],
        }}
        trigger={["click"]}
      >
        <Space
          aria-label="用户菜单"
          role="button"
          tabIndex={0}
          style={{ cursor: "pointer", color: dark ? "rgba(255,255,255,0.85)" : undefined }}
        >
          <Avatar size="small" icon={<UserOutlined />} />
          <span>{user?.username || "用户"}</span>
          <DownOutlined />
        </Space>
      </Dropdown>

      <Modal
        title="修改密码"
        open={passwordOpen}
        onOk={changePassword}
        onCancel={() => setPasswordOpen(false)}
        okText="确认修改"
      >
        <Form form={passwordForm} layout="vertical">
          <Form.Item name="old_password" label="旧密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[{ required: true }, { min: 6, message: "新密码不少于 6 位" }]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
