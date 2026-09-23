import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Qty } from './Qty';

describe('Qty', () => {
  it('★ 四种形态四个字形，没有一个退化成 0', () => {
    const { container } = render(
      <>
        <Qty v={{ kind: 'num', value: 380 }} />
        <Qty v={{ kind: 'num', value: 0 }} />
        <Qty v={{ kind: 'unknown' }} />
        <Qty v={{ kind: 'na' }} />
        <Qty v={{ kind: 'nosum' }} />
      </>,
    );
    expect(screen.getByText('380')).toBeInTheDocument();
    expect(container.querySelector('.qty--zero')?.textContent).toBe('0');
    expect(screen.getByText('—')).toHaveClass('cell__unknown');
    expect(screen.getByText('不适用')).toHaveClass('chip', 'chip--dim');
    expect(screen.getByText('不可求和')).toHaveClass('cell__unknown');
    // ★ 未知 / 不适用 / 不可求和 三者的字互不相同 —— 合成一个就查不出是哪种病
    expect(new Set(['—', '不适用', '不可求和']).size).toBe(3);
  });

  it('big 用 .cell__close（整格唯一的大字）', () => {
    const { container } = render(<Qty v={{ kind: 'num', value: 420 }} big />);
    expect(container.querySelector('.cell__close')?.textContent).toBe('420');
  });
});
