const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseWebPairing}=require('../managed-companion');

test('web pairing pins an exact secure origin, port and one-time nonce',()=>{
 const nonce='a'.repeat(64);
 const query=new URLSearchParams({nonce,origin:'https://jiraya.example',port:'8765'}).toString();
 assert.deepEqual(parseWebPairing({path:'/connect',query}),{nonce,origin:'https://jiraya.example',port:8765});
 for(const origin of ['http://remote.example','https://user:password@jiraya.example','https://jiraya.example/path','https://jiraya.example/?redirect=evil','javascript:alert(1)'])
  assert.throws(()=>parseWebPairing({path:'/connect',query:new URLSearchParams({nonce,origin}).toString()}));
 assert.throws(()=>parseWebPairing({path:'/connect',query:new URLSearchParams({nonce:'short',origin:'https://jiraya.example'}).toString()}));
 assert.throws(()=>parseWebPairing({path:'/connect',query:new URLSearchParams({nonce,origin:'https://jiraya.example',port:'80'}).toString()}));
});
