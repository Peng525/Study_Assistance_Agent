import { describe, it, expect, beforeEach } from "vitest";
import { useAuthStore, getToken } from "../store/auth";

describe("认证 store", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ token: null, user: null });
  });

  it("login 持久化 token 和 user", () => {
    useAuthStore.getState().login("abc", { user_id: 1, username: "admin", role: "admin" });
    expect(getToken()).toBe("abc");
    expect(useAuthStore.getState().user?.role).toBe("admin");
  });

  it("logout 清除状态", () => {
    useAuthStore.getState().login("abc", { user_id: 1, username: "admin", role: "admin" });
    useAuthStore.getState().logout();
    expect(getToken()).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
  });
});
