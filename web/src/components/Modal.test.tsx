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
