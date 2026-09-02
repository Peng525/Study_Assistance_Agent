import { Button, Input } from "antd";
import { RobotOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useLocation, useNavigate } from "react-router-dom";
import UserMenu from "./UserMenu";

interface TopNavProps {
  aiExpanded?: boolean;
  onToggleAI?: () => void;
}

export default function TopNav({ aiExpanded = false, onToggleAI }: TopNavProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const homeActive = location.pathname === "/";
  const coursesActive =
    location.pathname === "/courses" || location.pathname.startsWith("/course/");

  const tabClassName = (active: boolean) =>
    `top-nav__tab${active ? " top-nav__tab--active" : ""}`;

  return (
    <header className="top-nav">
      <div className="top-nav__left">
        <div className="top-nav__brand">AI 助学助手</div>
        <nav className="top-nav__tabs" aria-label="学习导航">
          <Link
            to="/"
            className={tabClassName(homeActive)}
            aria-current={homeActive ? "page" : undefined}
          >
            首页
          </Link>
          <Link
            to="/courses"
            className={tabClassName(coursesActive)}
            aria-current={coursesActive ? "page" : undefined}
          >
            课程列表
          </Link>
        </nav>
      </div>
      <Input
        className="top-nav__search"
        placeholder="搜索课程 / 主题"
        prefix={<SearchOutlined />}
        onPressEnter={(e) => {
          const q = (e.target as HTMLInputElement).value;
          navigate(`/courses?q=${encodeURIComponent(q)}`);
        }}
      />
      <div className="top-nav__right">
        {onToggleAI && (
          <Button
            type="text"
            className={`top-nav__ai-toggle${aiExpanded ? " top-nav__ai-toggle--active" : ""}`}
            icon={<RobotOutlined />}
            aria-label={aiExpanded ? "收起 AI 对话" : "展开 AI 对话"}
            aria-pressed={aiExpanded}
            onClick={onToggleAI}
          >
            AI 对话
          </Button>
        )}
        <UserMenu showAdminEntry showThemeSettings />
      </div>
    </header>
  );
}
