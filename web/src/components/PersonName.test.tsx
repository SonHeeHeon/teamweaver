import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PersonName, ProjectCodes } from "./PersonName";

describe("후보 이름·사업 코드(화면 친절하게 2026-10-20)", () => {
  it("이름을 누르면 기술 창, 바깥을 누르면 닫힌다", () => {
    render(<PersonName name="가나다" person={{ id: "A", name: "가나다", grade: "고급", skills: { Java: 3 } }} />);
    fireEvent.click(screen.getByRole("button", { name: "가나다 기술 보기" }));
    expect(screen.getByRole("dialog", { name: "가나다 기술" }).textContent).toContain("Java 레벨 3");
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("사람 정보가 없으면 이름만", () => {
    render(<PersonName name="라마바" person={null} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("라마바")).toBeInTheDocument();
  });

  it("사업 코드에 마우스를 올리면 이름", () => {
    render(<p><ProjectCodes ids={["J1", "J2"]} projects={[{ id: "J1", name: "포털" }] as never} />에서 빼 옴</p>);
    expect(screen.getByLabelText("J1 포털")).toHaveAttribute("title", "포털");
    expect(screen.getByText("J2")).toBeInTheDocument();
  });
});
