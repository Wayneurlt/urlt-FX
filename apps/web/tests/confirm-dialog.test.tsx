import * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { ApiError } from "@/lib/api/client";

function Harness({ onConfirm, phrase = "LIQUIDATE" }: { onConfirm: (t?: string) => Promise<void> | void; phrase?: string | null }) {
  const [open, setOpen] = React.useState(true);
  return (
    <>
      <span data-testid="state">{open ? "open" : "closed"}</span>
      <ConfirmDialog open={open} onOpenChange={setOpen} title="Close cycle" description="Closes all legs" phrase={phrase} confirmLabel="Do it" destructive onConfirm={onConfirm} />
    </>
  );
}

describe("ConfirmDialog", () => {
  it("is an accessible dialog with title and description", () => {
    render(<Harness onConfirm={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Close cycle" });
    expect(dialog).toHaveAccessibleDescription("Closes all legs");
  });

  it("enables confirm only when the exact phrase is typed and passes it through", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<Harness onConfirm={onConfirm} />);
    const btn = screen.getByRole("button", { name: "Do it" });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/to confirm/), "LIQUIDAT");
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/to confirm/), "E");
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledWith("LIQUIDATE");
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("closed"));
  });

  it("stays open and shows the API error when the action fails", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new ApiError(403, "forbidden", "Admin role required", null));
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.type(screen.getByLabelText(/to confirm/), "LIQUIDATE");
    await userEvent.click(screen.getByRole("button", { name: "Do it" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Admin role required");
    expect(screen.getByTestId("state")).toHaveTextContent("open");
  });

  it("works without a phrase and cancel closes without confirming", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} phrase={null} />);
    expect(screen.getByRole("button", { name: "Do it" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("state")).toHaveTextContent("closed");
  });
});
