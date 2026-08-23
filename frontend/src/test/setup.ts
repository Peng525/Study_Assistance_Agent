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

// localStorage 在 jsdom 中可用，但确保稳定
export {};
