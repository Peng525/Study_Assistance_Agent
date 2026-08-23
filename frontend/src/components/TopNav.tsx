import { useState } from "react";
import { Avatar, Dropdown, Form, Input, Modal, Space, message } from "antd";
import { DownOutlined, SearchOutlined, UserOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { useAuthStore } from "../store/auth";
import { api } from "../api/client";
import ThemeSwitch from "./ThemeSwitch";

export default function TopNav() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const [pwdOpen, setPwdOpen] = useState(false);
  const [pwdForm] = Form.useForm();

  const changePassword = async () => {
    const { old_password, new_password } = await pwdForm.validateFields();
    try {
      await api.post("/auth/change-password", { old_password, new_password });
      message.success("密码修改成功，请重新登录");
      setPwdOpen(false);
      pwdForm.resetFields();
      logout();
      navigate("/login");
    } catch (e: any) {
      message.error(e.response?.data?.detail || "修改失败");
    }
  };

  const menuItems = [
    {
      key: "password",
      label: "修改密码",
      onClick: () => {
        pwdForm.resetFields();
        setPwdOpen(true);
      },
    },
    ...(user?.role === "admin"
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
      onClick: () => {
        logout();
        navigate("/login");
      },
    },
  ];

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 24,
        padding: "8px 24px",
        background: "var(--bg-header)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 18 }}>AI 助学助手</div>
      <Space>
        <Link to="/">首页</Link>
        <Link to="/courses">课程列表</Link>
      </Space>
      <Input
        placeholder="搜索课程 / 主题"
        prefix={<SearchOutlined />}
        style={{ maxWidth: 260, marginLeft: "auto" }}
        onPressEnter={(e) => {
          const q = (e.target as HTMLInputElement).value;
          navigate(`/courses?q=${encodeURIComponent(q)}`);
        }}
      />
      <ThemeSwitch />
      <Dropdown menu={{ items: menuItems }}>
        <Space style={{ cursor: "pointer" }}>
          <Avatar size="small" icon={<UserOutlined />} />
          <span>{user?.username}</span>
          <DownOutlined />
        </Space>
      </Dropdown>

      <Modal
        title="修改密码"
        open={pwdOpen}
        onOk={changePassword}
        onCancel={() => setPwdOpen(false)}
        okText="确认修改"
      >
        <Form form={pwdForm} layout="vertical">
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
    </div>
  );
}
