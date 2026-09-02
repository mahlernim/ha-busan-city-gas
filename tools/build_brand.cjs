/* Render original project artwork with a licensed Lucide flame path. */
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');
const root = path.resolve(__dirname, '..');
const flame = 'M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4';
const mark = (dark) => `<rect x="8" y="8" width="240" height="240" rx="56" fill="${dark ? '#123e38' : '#126b54'}"/><g transform="translate(41 27) scale(7.25)" fill="none" stroke="${dark ? '#c3f4dd' : '#f5fff8'}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="${flame}"/></g><path d="M77 211h102" stroke="#e9b857" stroke-width="10" stroke-linecap="round"/>`;
async function build() {
  for (const dark of [false, true]) {
    const prefix = dark ? 'dark_' : '';
    const icon = `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">${mark(dark)}</svg>`;
    const logo = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="200" viewBox="0 0 900 200"><g transform="translate(0 4) scale(.75)">${mark(dark)}</g><text x="218" y="91" fill="${dark ? '#eef7f3' : '#193d32'}" font-family="Malgun Gothic, Noto Sans CJK KR, sans-serif" font-size="56" font-weight="700">부산도시가스</text><text x="220" y="140" fill="${dark ? '#b1c9c0' : '#526c60'}" font-family="Malgun Gothic, Noto Sans CJK KR, sans-serif" font-size="25">Home Assistant · 비공식 연동</text></svg>`;
    for (const [name, svg, width] of [['icon', icon, 256], ['logo', logo, 900]]) {
      for (const scale of [1, 2]) {
        const filename = `${prefix}${name}${scale === 2 ? '@2x' : ''}.png`;
        const buffer = await sharp(Buffer.from(svg), {density: 144}).resize(width * scale).png().toBuffer();
        for (const target of ['brand', 'custom_components/busan_city_gas/brand']) {
          fs.mkdirSync(path.join(root, target), {recursive:true});
          fs.writeFileSync(path.join(root, target, filename), buffer);
        }
      }
    }
  }
  fs.copyFileSync(path.join(root,'brand/NOTICE.txt'),path.join(root,'custom_components/busan_city_gas/brand/NOTICE.txt'));
}
build().catch(error => {console.error(error); process.exitCode=1;});
