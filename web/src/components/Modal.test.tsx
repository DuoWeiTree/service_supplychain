import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';

describe('Modal', () => {
  it('★ 只用一套结构：.modal-backdrop > .modal，页面里不出现 <dialog>', () => {
    const { container } = render(<Modal title="撤销 rev 2" onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal-backdrop > .modal')).not.toBeNull();
    expect(container.querySelector('dialog')).toBeNull();
  });

  it('danger 的弹窗长得不一样（原则四）', () => {
    const { container } = render(<Modal title="撤销 rev 2" danger onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal__warn')).not.toBeNull();
  });

  it('Esc 关闭', async () => {
    const onClose = vi.fn();
    render(<Modal title="撤销 rev 2" onClose={onClose}><p>x</p></Modal>);
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe('Modal warn 参数', () => {
  it('danger 且未给 warn → 保持原文案（PlanRevs 依赖）', () => {
    const { container } = render(<Modal title="撤销" danger onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal__warn')?.textContent).toBe('⛔ 这一步会撤销已提交的记录');
  });

  it('给了 warn → 用 warn', () => {
    const { container } = render(<Modal title="下单" danger warn="⛔ 下单后不可撤回" onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal__warn')?.textContent).toBe('⛔ 下单后不可撤回');
  });

  it('没有 danger → 即使给了 warn 也不显示', () => {
    const { container } = render(<Modal title="x" warn="⛔ y" onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal__warn')).toBeNull();
  });
});
