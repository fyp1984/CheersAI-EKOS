(() => {
  'use strict';

  // Double-run guard: the deployment bootstrap may inject this script tag
  // more than once across volume rebuilds. The shell must stay single-instance.
  if (window.__FILEBAY_SHELL_MOUNTED__) return;
  window.__FILEBAY_SHELL_MOUNTED__ = true;

  // Deployment-only brand surface for the 现代金租 delivery: the workspace is
  // presented as the EKOS product under the CheersAI brand. This adapter does
  // not modify upstream RAGFlow source or behaviour.
  // Version: 2026-09-12 (v2: title/favicon/brand, single-instance guard).
  const BRAND = Object.freeze({
    title: 'EKOS｜企业知识操作系统 · CheersAI',
    favicon: '/ragflow/cheersai-logo.svg',
    faviconVersion: '20260912',
    logoAlt: 'EKOS｜企业知识操作系统',
    returnLabel: '返回 CheersAI EKOS 知识库工作台',
  });

  // Keep the upstream SPA in Chinese/light mode before React performs its
  // first render.
  try {
    window.localStorage.setItem('ragflow-ui-theme', 'light');

    const languageMigration = 'filebay-ragflow-default-language';
    if (window.localStorage.getItem(languageMigration) !== 'zh-Hans') {
      window.localStorage.setItem('lng', 'zh-Hans');
      window.localStorage.setItem(languageMigration, 'zh-Hans');
    }
  } catch {
    // Managed browsers can block storage. RAGFlow remains usable in that case.
  }

  document.documentElement.classList.add('filebay-ragflow-shell');

  const fileBayUrl = () => {
    const configured = document.documentElement.dataset.filebayUrl;
    return configured || `${window.location.origin}/knowledge#knowledge-engine`;
  };

  const assetUrl = (name) => `${window.location.origin}/ragflow/${name}`;

  const findKnowledgeLink = () =>
    Array.from(document.querySelectorAll('header nav > ul a')).find((link) => {
      try {
        return new URL(link.href, window.location.origin).pathname.endsWith('/datasets');
      } catch {
        return false;
      }
    });

  // The upstream SPA rewrites document.title on every route change, so the
  // title is reasserted by the shared MutationObserver in renderShell.
  const applyTitle = () => {
    if (document.title !== BRAND.title) {
      document.title = BRAND.title;
    }
  };

  const applyFavicon = () => {
    const wanted = `${BRAND.favicon}?v=${BRAND.faviconVersion}`;
    const links = document.querySelectorAll('link[rel~="icon" i]');
    if (links.length === 0) {
      const link = document.createElement('link');
      link.rel = 'icon';
      link.type = 'image/svg+xml';
      link.href = wanted;
      document.head.append(link);
      return;
    }
    links.forEach((link) => {
      if (link.getAttribute('href') !== wanted) {
        link.type = 'image/svg+xml';
        link.setAttribute('href', wanted);
      }
    });
  };

  const renderBrand = () => {
    const logo = document.querySelector('header img[alt="RAGFlow logo"]');
    if (!logo) return false;

    logo.src = assetUrl('cheersai-logo.svg');
    logo.alt = BRAND.logoAlt;

    const logoLink = logo.closest('a');
    if (logoLink) {
      logoLink.href = fileBayUrl();
      logoLink.setAttribute('aria-label', BRAND.returnLabel);
    }
    return true;
  };

  const renderWorkbenchNav = () => {
    const existing = document.getElementById('filebay-workbench-nav');
    if (existing?.isConnected) return true;

    // Match the upstream dataset route rather than a hard-coded item index;
    // future RAGFlow navigation ordering therefore remains compatible.
    const knowledgeLink = findKnowledgeLink();
    const navList = knowledgeLink?.closest('ul');
    const knowledgeItem = knowledgeLink?.closest('li');
    if (!navList || !knowledgeItem) return false;

    const item = document.createElement('li');
    item.id = 'filebay-workbench-nav';

    const link = document.createElement('a');
    link.className = knowledgeLink.className;
    link.href = fileBayUrl();
    link.textContent = '工作台';
    link.setAttribute('aria-label', BRAND.returnLabel);
    item.append(link);

    navList.insertBefore(item, knowledgeItem);
    return true;
  };

  let renderScheduled = false;
  const renderShell = () => {
    if (renderScheduled) return;
    renderScheduled = true;
    window.queueMicrotask(() => {
      renderScheduled = false;
      applyTitle();
      applyFavicon();
      renderBrand();
      renderWorkbenchNav();
    });
  };

  const keepShellMounted = () => {
    renderShell();

    // React rebuilds the header and the document title after route and
    // session changes. Reapply the deployment-only chrome without touching
    // RAGFlow's own React tree.
    const observer = new MutationObserver(renderShell);
    observer.observe(document.documentElement, { childList: true, subtree: true });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', keepShellMounted, { once: true });
  } else {
    keepShellMounted();
  }
})();
