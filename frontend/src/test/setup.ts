import "@testing-library/jest-dom";

// jsdom 缺少 matchMedia，为 antd 响应式组件添加 polyfill
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList => {
    return {
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList;
  };
}

// jsdom 不支持带 pseudoElt 的 getComputedStyle；antd 表格只需要元素本身的样式。
const nativeGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element: Element) => nativeGetComputedStyle(element);

// localStorage 在 jsdom 中可用，但确保稳定
export {};
