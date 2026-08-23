import { useEffect, useState } from "react";
import { Button, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { api } from "../../api/client";
import { useAuthStore } from "../../store/auth";

interface UserRow {
  id: number;
  username: string;
  role: string;
  created_at?: string;
}

export default function Users() {
  const [list, setList] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(false);
  const me = useAuthStore((s) => s.user);

  const load = () => {
    setLoading(true);
    api
      .get("/admin/users")
      .then((r) => setList(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const reset = async (row: UserRow) => {
    try {
      await api.post(`/admin/users/${row.id}/reset-password`);
      message.success(`已重置 ${row.username} 密码为 123456`);
    } catch (e: any) {
      message.error(e.response?.data?.detail || "重置失败");
    }
  };

  const columns = [
    { title: "ID", dataIndex: "id" },
    { title: "用户名", dataIndex: "username" },
    {
      title: "角色",
      dataIndex: "role",
      render: (v: string) => (v === "admin" ? <Tag color="blue">admin</Tag> : <Tag>user</Tag>),
    },
    { title: "创建时间", dataIndex: "created_at", render: (v: string) => v || "—" },
    {
      title: "操作",
      render: (_: any, row: UserRow) => (
        <Popconfirm
          title={`重置 ${row.username} 的密码为 123456？`}
          onConfirm={() => reset(row)}
          disabled={row.id === me?.user_id}
        >
          <Button size="small" disabled={row.id === me?.user_id}>
            重置密码
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <Typography.Title level={4}>用户管理</Typography.Title>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={list} pagination={false} />
    </div>
  );
}
