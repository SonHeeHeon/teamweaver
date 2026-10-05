import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AdminLogin } from "./AdminLogin";

vi.mock("../api/client", () => ({ adminLogin: vi.fn() }));
import { adminLogin } from "../api/client";

describe("AdminLogin", () => {
  it("비밀번호가 맞으면 onLoggedIn을 부르고 입력을 비운다", async () => {
    vi.mocked(adminLogin).mockImplementation(async () => {});
    const onLoggedIn = vi.fn();
    render(<AdminLogin onLoggedIn={onLoggedIn} />);
    const input = screen.getByLabelText("비밀번호") as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));
    await waitFor(() => expect(onLoggedIn).toHaveBeenCalled());
    expect(adminLogin).toHaveBeenCalledWith("pw");
    expect(input.value).toBe("");
  });

  it("틀리거나 잠기면 서버 이유를 보여 주고 onLoggedIn을 부르지 않는다", async () => {
    vi.mocked(adminLogin).mockImplementation(async () => {
      throw new Error("비밀번호를 여러 번 틀려 60초 동안 잠겼다.");
    });
    const onLoggedIn = vi.fn();
    render(<AdminLogin onLoggedIn={onLoggedIn} />);
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("60초 동안 잠겼다");
    expect(onLoggedIn).not.toHaveBeenCalled();
  });

  it("비밀번호가 비어 있으면 보내지 않는다", () => {
    render(<AdminLogin onLoggedIn={vi.fn()} />);
    expect(screen.getByRole("button", { name: "로그인" })).toBeDisabled();
  });
});
