import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AppShell } from './AppShell';
import { getActor, setActor } from './actorStore';
import { ACTORS } from './actors';

beforeEach(() => setActor(ACTORS[0]!.actor_id));

describe('AppShell', () => {
  it('顶栏有 actor 下拉，选了就改当前操作人', async () => {
    render(<MemoryRouter><AppShell crumb="我的计划"><p>x</p></AppShell></MemoryRouter>);
    const select = screen.getByLabelText('操作人');
    await userEvent.selectOptions(select, 'ops.li');
    expect(getActor()).toBe('ops.li');
  });

  it('★ 屏上标「留痕可伪造」—— 无认证这件事不许只写在文档里', () => {
    render(<MemoryRouter><AppShell crumb="我的计划"><p>x</p></AppShell></MemoryRouter>);
    expect(screen.getByText('留痕可伪造')).toBeInTheDocument();
  });

  it('★ 不写功能描述：顶栏与面包屑之外没有解说文字', () => {
    const { container } = render(<MemoryRouter><AppShell crumb="我的计划"><p>x</p></AppShell></MemoryRouter>);
    const bar = container.querySelector('.shell__bar')!;
    // 每段可见文字都必须 ≤ 8 字（品牌名 / 面包屑 / 状态标记），一句话解说必然超
    const longs = Array.from(bar.querySelectorAll('*'))
      .filter((el) => el.children.length === 0)
      .map((el) => el.textContent?.trim() ?? '')
      .filter((s) => s.length > 8);
    expect(longs).toEqual([]);
  });
});
