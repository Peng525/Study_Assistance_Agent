import { Dropdown, Button } from "antd";
import { MoonOutlined, SunOutlined, DesktopOutlined, DownOutlined } from "@ant-design/icons";
import { useThemeStore } from "../store/theme";
import { ThemeMode } from "../theme/theme";

const options: { key: ThemeMode; label: string; icon: React.ReactNode }[] = [
  { key: "light", label: "浅色", icon: <SunOutlined /> },
  { key: "dark", label: "深色", icon: <MoonOutlined /> },
  { key: "system", label: "系统跟随", icon: <DesktopOutlined /> },
];

export default function ThemeSwitch() {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);
  const current = options.find((o) => o.key === mode)!;

  return (
    <Dropdown
      menu={{
        items: options.map((o) => ({
          key: o.key,
          label: (
            <span>
              {o.icon} {o.label}
            </span>
          ),
          onClick: () => setMode(o.key),
        })),
        selectable: true,
        selectedKeys: [mode],
      }}
    >
      <Button type="text" icon={current.icon}>
        {current.label} <DownOutlined />
      </Button>
    </Dropdown>
  );
}
