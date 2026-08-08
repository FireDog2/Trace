import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./Markdown";


describe("Markdown", () => {
  it("renders emphasis, lists, tables, code, and artifact links without raw markers", () => {
    const { container } = render(<Markdown content={`## 关键发现

**利润下滑** 12%，但 *收入稳定*。

- 物流成本上升
- [证据](artifact://artifact-1)

| 指标 | 数值 |
| --- | --- |
| 利润 | -12% |

\`gross_profit\``} />);

    expect(screen.getByRole("heading", { name: "关键发现" })).toBeVisible();
    expect(screen.getByText("利润下滑").tagName).toBe("STRONG");
    expect(screen.getByText("收入稳定").tagName).toBe("EM");
    expect(screen.getByRole("list")).toBeVisible();
    expect(screen.getByRole("link", { name: "证据" })).toHaveAttribute(
      "href", "/api/artifacts/artifact-1/content",
    );
    expect(within(screen.getByRole("table")).getByText("-12%")).toBeVisible();
    expect(screen.getByText("gross_profit").tagName).toBe("CODE");
    expect(container).not.toHaveTextContent("**利润下滑**");
  });

  it("does not turn untrusted link schemes or raw HTML into active content", () => {
    const { container } = render(
      <Markdown content={'[危险链接](javascript:alert(1))\n\n<img src=x onerror=alert(1)>'} />,
    );

    expect(screen.queryByRole("link", { name: "危险链接" })).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
  });
});
