import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsTab } from "./SettingsTab";
import type { SettingsResponse } from "../api/types";

const DATA: SettingsResponse = {
  settings: { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
              time_limit: 120, gap: 0.05 },
  defaults: { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
              time_limit: 120, gap: 0.05 },
  bounds: { min_alloc: { min: 0.05, max: 1 }, clique_threshold_months: { min: 1, max: 24 },
            lam: { min: 0, max: 1 }, mu: { min: 0, max: 1 }, time_limit: { min: 5, max: 600 },
            gap: { min: 0, max: 0.2 } },
  updated_at: null,
  load_error: null,
};

describe("SettingsTab", () => {
  it("비율은 %로 보여 주고 저장할 때 0~1로 되돌린다", async () => {
    const onSave = vi.fn(async () => {});
    render(<SettingsTab data={DATA} onSave={onSave} />);
    const minAlloc = screen.getByLabelText(/최소 투입률/) as HTMLInputElement;
    expect(minAlloc.value).toBe("30");
    expect((screen.getByLabelText(/허용 최적성 차이/) as HTMLInputElement).value).toBe("5");
    fireEvent.change(minAlloc, { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ ...DATA.settings, min_alloc: 0.25 }));
    expect(await screen.findByText(/다음 '최적화 실행'부터/)).toBeInTheDocument();
  });

  it("서버 범위 밖·정수 아님·빈 값은 저장을 막고 이유를 보여 준다", () => {
    render(<SettingsTab data={DATA} onSave={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/최소 투입률/), { target: { value: "3" } });
    expect(screen.getByText("5~100% 범위여야 한다")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/최소 투입률/), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText(/계산 시간 한도/), { target: { value: "12.5" } });
    expect(screen.getByText("정수여야 한다")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/계산 시간 한도/), { target: { value: "" } });
    expect(screen.getByText("값을 입력해야 한다")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("0x10·1e1 같은 표기는 숫자로 받지 않는다", () => {
    render(<SettingsTab data={DATA} onSave={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/계산 시간 한도/), { target: { value: "0x10" } });
    expect(screen.getByText("숫자가 아니다")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/계산 시간 한도/), { target: { value: "1e2" } });
    expect(screen.getByText("숫자가 아니다")).toBeInTheDocument();
  });

  it("소수 둘째 자리 비율도 그대로 보이고, 손대지 않으면 저장 대상이 아니다", () => {
    render(<SettingsTab data={{ ...DATA, settings: { ...DATA.settings, min_alloc: 0.1234 } }}
                        onSave={vi.fn()} />);
    expect((screen.getByLabelText(/최소 투입률/) as HTMLInputElement).value).toBe("12.34");
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("경계값(100%)은 받아들인다", () => {
    render(<SettingsTab data={DATA} onSave={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/최소 투입률/), { target: { value: "100" } });
    expect(screen.queryByText(/범위여야 한다/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  });

  it("바뀐 값이 없으면 저장 버튼이 비활성이다", () => {
    render(<SettingsTab data={DATA} onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("기본값으로 채우기는 서버 기본값을 폼에 넣는다(저장은 따로)", () => {
    const onSave = vi.fn();
    render(<SettingsTab data={{ ...DATA, settings: { ...DATA.settings, min_alloc: 0.5 } }}
                        onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "기본값으로 채우기" }));
    expect((screen.getByLabelText(/최소 투입률/) as HTMLInputElement).value).toBe("30");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("설정 파일 오류를 경고로 보여 준다", () => {
    render(<SettingsTab data={{ ...DATA, load_error: "설정 파일 settings.json을 읽지 못해" }}
                        onSave={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("settings.json");
  });

  it("저장 실패를 알린다", async () => {
    const onSave = vi.fn(async () => { throw new Error("설정 저장 실패(422)"); });
    render(<SettingsTab data={DATA} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText(/최소 투입률/), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/설정 저장 실패\(422\)/)).toBeInTheDocument();
  });
});


describe("SettingsTab — 서버 값이 바뀌면", () => {
  it("폼을 새 서버 값으로 다시 채워, 손대지 않은 필드를 옛 값으로 되돌려 저장하지 않는다", async () => {
    const onSave = vi.fn(async () => {});
    const { rerender } = render(<SettingsTab data={DATA} onSave={onSave} />);
    // 다른 관리자가 gap을 10%로 저장했고, 화면이 새 값을 받았다.
    rerender(<SettingsTab data={{ ...DATA, settings: { ...DATA.settings, gap: 0.1 },
                                  updated_at: "2026-10-05T01:00:00+00:00" }}
                          onSave={onSave} />);
    expect((screen.getByLabelText(/허용 최적성 차이/) as HTMLInputElement).value).toBe("10");
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/최소 투입률/), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(
      { ...DATA.settings, gap: 0.1, min_alloc: 0.4 }));
  });

  it("소수 셋째 자리 비율은 받지 않는다", () => {
    render(<SettingsTab data={DATA} onSave={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/최소 투입률/), { target: { value: "30.555" } });
    expect(screen.getByText("소수 둘째 자리까지만 입력할 수 있다")).toBeInTheDocument();
  });
});
