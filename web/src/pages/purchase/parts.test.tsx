import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { CommitButton, GateList, StatusPair, gateDetail } from './parts';
import type { LxObservation } from '../../api/types';

const LX = (over: Partial<LxObservation> = {}): LxObservation => ({ status: 2, status_shipped: 1, quantity_real: null,
  quantity_receive: null, quantity_entry: null, source: 'api', captured_at: '2026-10-10T14:50:00+08:00', call_id: 9, ...over });

describe('CommitButton', () => {
  it('⛔ 标 + 独立样式；被挡时左边点名原因', () => {
    render(<CommitButton disabled hint="供应商 未过">下单</CommitButton>);
    const b = screen.getByRole('button', { name: /下单/ });
    expect(b).toHaveClass('sc-commit');
    expect(b).toHaveTextContent('⛔');
    expect(b).toBeDisabled();
    expect(screen.getByTestId('place-blocked')).toHaveTextContent('供应商 未过');
    expect(b).toHaveAccessibleDescription('供应商 未过');   // 读屏能听到为什么点不了
  });
});

describe('StatusPair', () => {
  it('草稿：领星一侧写「未下单」', () => {
    render(<StatusPair local="草稿" lx={null} />);
    expect(screen.getByTestId('lx-status')).toHaveTextContent('未下单');
  });
  it('已下单没读回过：「未读回」；读回中：「正在向领星查询…」', () => {
    const { rerender } = render(<StatusPair local="已下单" lx={null} />);
    expect(screen.getByTestId('lx-status')).toHaveTextContent('未读回');
    rerender(<StatusPair local="已下单" lx={null} reading />);
    expect(screen.getByTestId('lx-status')).toHaveTextContent('正在向领星查询…');
  });
  it('读回过：状态 + 到货 + 读回时间；作废标红；认不出的码原样写', () => {
    const { rerender } = render(<StatusPair local="已下单" lx={LX({ status: 2, status_shipped: 2 })} />);
    const s = screen.getByTestId('lx-status');
    expect(s).toHaveTextContent('待到货 部分到货 读回于 2026-10-10 14:50');
    rerender(<StatusPair local="已下单" lx={LX({ status: -1, status_shipped: null })} />);
    expect(within(screen.getByTestId('lx-status')).getByText('作废')).toHaveClass('sp-v--danger');
    rerender(<StatusPair local="已下单" lx={LX({ status: 777, status_shipped: null })} />);
    expect(screen.getByTestId('lx-status')).toHaveTextContent('状态码 777');
  });
  it('读回失败：有上次观察时写明显示的是上次的；没有就不这么说', () => {
    const { rerender } = render(<StatusPair local="已下单" lx={LX()} readError="not_sent" />);
    expect(screen.getByRole('alert')).toHaveTextContent('这次读回失败（not_sent），显示的是上次读回的状态');
    rerender(<StatusPair local="已下单" lx={null} readError="not_sent" />);
    expect(screen.getByRole('alert')).toHaveTextContent('这次读回失败（not_sent）');
    expect(screen.getByRole('alert')).not.toHaveTextContent('上次');
  });
});

describe('GateList', () => {
  it('未过的排前面，头部写「N 道未过」与检查来源，每条带后端闸名与原因', () => {
    render(<GateList source="服务端检查" gates={[
      { gate: 'supplier', ok: true, detail: {} },
      { gate: 'line_units', ok: false, detail: { line_no: 2, units: 5, sources: 8 } },
      { gate: 'sources_orderable', ok: false, detail: { offenders: [{ line_id: 101, state: '已撤销' }] } },
    ]} />);
    expect(screen.getByTestId('gate-summary')).toHaveTextContent('2 道未过');
    expect(screen.getByText('服务端检查')).toBeInTheDocument();
    const items = screen.getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('第 2 行数量不少于来源合计');
    expect(items[0]).toHaveTextContent('数量 5 < 来源合计 8');
    expect(items[0]).toHaveTextContent('line_units');
    expect(items[1]).toHaveTextContent('记录 #101 已撤销');
    expect(items[2]).toHaveClass('is-ok');
  });
  it('全过：「全部通过」', () => {
    render(<GateList source="服务端检查" gates={[{ gate: 'supplier', ok: true, detail: {} }]} />);
    expect(screen.getByTestId('gate-summary')).toHaveTextContent('全部通过');
  });
  it('认不出的 detail 形状不编原因', () => {
    expect(gateDetail({ gate: 'single_wid', ok: false, detail: {} })).toBeNull();
    expect(gateDetail({ gate: 'single_wid', ok: false, detail: { wids: [1, 2] } })).toBe('明细分属仓 1、2');
  });
});
