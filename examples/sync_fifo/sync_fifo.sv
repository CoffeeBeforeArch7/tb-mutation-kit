// 同步FIFO：DEPTH = 2^AW；读数据在 rd_en 之后的那个时钟沿更新
module sync_fifo #(
  parameter WIDTH = 8,
  parameter AW    = 3,
  parameter DEPTH = 8
) (
  input  wire             clk,
  input  wire             rst,
  input  wire             wr_en,
  input  wire [WIDTH-1:0] wr_data,
  input  wire             rd_en,
  output reg  [WIDTH-1:0] rd_data,
  output wire             full,
  output wire             empty
);
  reg [WIDTH-1:0] mem [0:DEPTH-1];
  reg [AW:0]      wr_ptr, rd_ptr;   // 多一位区分空和满

  wire do_wr = wr_en && !full;
  wire do_rd = rd_en && !empty;

  assign empty = (wr_ptr == rd_ptr);
  assign full  = (wr_ptr[AW] != rd_ptr[AW]) && (wr_ptr[AW-1:0] == rd_ptr[AW-1:0]);

  always @(posedge clk) begin
    if (rst) begin
      wr_ptr <= 0;
      rd_ptr <= 0;
    end else begin
      if (do_wr) begin
        mem[wr_ptr[AW-1:0]] <= wr_data;
        wr_ptr <= wr_ptr + 1;
      end
      if (do_rd) begin
        rd_data <= mem[rd_ptr[AW-1:0]];
        rd_ptr <= rd_ptr + 1;
      end
    end
  end
endmodule
